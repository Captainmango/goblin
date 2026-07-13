from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, Protocol, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from openhands.sdk import Agent, LocalConversation, LocalWorkspace
from openhands.sdk.conversation import get_agent_final_response
from goblin.conversation import run_conversation
from goblin.cost_control import CircuitBreaker, LimitExceeded
from goblin.enums import LoadProjectStatus
from goblin.prompts import BASE_SYSTEM_PROMPT, JSON_REPAIR_PROMPT
from goblin.utils.llm import build_llm
from goblin.tools.tool_map import build_tool_filter_regex, resolve_tools

logger = logging.getLogger(__name__)


MAX_PARSE_RETRIES = 3


class PhaseResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool
    message: list


def _validate_output_schema(data: dict, schema: dict) -> list[str]:
    """Validate *data* against a JSON Schema fragment and return human-readable errors."""
    from jsonschema import Draft7Validator

    validator = Draft7Validator(schema)
    return [e.message for e in validator.iter_errors(data)]


def _extract_json(text: str) -> dict:
    """Extract JSON from text, handling markdown code blocks."""
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())
    return json.loads(text.strip())


if TYPE_CHECKING:
    from goblin.blueprint import V1Blueprint


class PhaseProtocol(Protocol):
    id: str
    condition: str | None
    foreach: str | None
    on_failure: dict | None
    depends_on: list[str] | None

    async def run(self, context: dict) -> Dict: ...


class PromptPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.prompt_template: str = raw["prompt"]
        self.allowed_tools: list[str] = raw.get("allowed_tools", [])
        self.hooks_config: dict = raw.get("hooks", {})
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self.mcp_servers_config: dict | None = raw.get("mcp_servers")
        self.history: list[str] | None = raw.get("history")
        self.max_iterations: int | None = raw.get("max_iterations")
        self.max_tokens: int | None = raw.get("max_tokens")
        self.max_cost: float | None = raw.get("max_cost")
        self.model: str | None = raw.get("model")
        self.llm_config: dict | None = raw.get("llm")
        self.depends_on: list[str] | None = raw.get("depends_on")
        self.output_schema: dict | None = raw.get("output_schema")
        self.worktree_config: dict | None = raw.get("worktree")
        self._blueprint = blueprint

    async def run(self, context: dict) -> Dict:
        prompt = self.prompt_template.format(**context)

        history_context = context.get("_history_context", "")
        if history_context:
            prompt = history_context + prompt

        worktree_config = self._blueprint._resolve_worktree_config(self)
        if "Worktree" in self.allowed_tools and worktree_config is None:
            return {
                "success": False,
                "message": [
                    "Worktree tool is listed in allowed_tools but no worktree configuration is defined. "
                    "Add `defaults.worktree` or `phase.worktree` to the blueprint."
                ],
                "prompt": prompt,
                "output": "",
                "spend": {},
            }

        tool_params: dict[str, dict] = {}
        if worktree_config is not None and "Worktree" in self.allowed_tools:
            tool_params["Worktree"] = {
                "repo_root": self._blueprint.project_path or os.getcwd(),
                "base_dir": worktree_config.get("base_dir", ".goblin/worktrees"),
                "deps": worktree_config.get("deps", []),
                "auto_link": worktree_config.get("auto_link", False),
            }

        system_prompt = BASE_SYSTEM_PROMPT
        if self._blueprint.project_refs:
            system_prompt = system_prompt + "\n\n" + self._blueprint.project_refs

        breaker = CircuitBreaker(
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            max_cost=self.max_cost,
        )

        llm = build_llm(model=self._blueprint._resolve_llm_config(self, context))
        agent = Agent(
            llm=llm,
            tools=resolve_tools(self.allowed_tools, tool_params=tool_params),
            mcp_config=self._blueprint._build_mcp_servers(self, context),
            system_prompt=system_prompt,
            filter_tools_regex=build_tool_filter_regex(self.allowed_tools),
            critic=None,
        )

        workspace = self._blueprint._get_workspace(context)

        try:
            conversation = await run_conversation(
                agent,
                workspace,
                prompt,
                hook_config=self._blueprint._build_hooks(self),
                max_iterations=self.max_iterations or 500,
                max_cost=self.max_cost,
            )
            breaker.absorb(conversation)
        except LimitExceeded as exc:
            return {
                "success": False,
                "message": [f"Circuit breaker: {exc.reason} limit exceeded"],
                "prompt": prompt,
                "output": "",
                "spend": exc.tracker,
            }

        text = get_agent_final_response(conversation.state.events)

        if not text:
            return {
                "success": False,
                "message": ["Agent produced no text output"],
                "prompt": prompt,
                "output": "",
                "spend": breaker.tracker,
            }

        parsed = await self._parse_response(text)

        if self.output_schema is not None:
            schema_errors = _validate_output_schema(parsed, self.output_schema)
            if schema_errors:
                logger.warning(
                    "Output schema validation failed",
                    extra={
                        "event": "phase.output_schema_failed",
                        "phase_id": self.id,
                        "errors": schema_errors,
                    },
                )
                return {
                    "success": False,
                    "message": [f"Output schema validation failed: {e}" for e in schema_errors],
                    "prompt": prompt,
                    "output": text,
                    "spend": breaker.tracker,
                }

        return {**parsed, "prompt": prompt, "output": text, "spend": breaker.tracker}

    async def _parse_response(self, text: str, retries: int = MAX_PARSE_RETRIES) -> Dict:
        """Parse agent response JSON, retrying with a repair prompt on failure."""
        for attempt in range(1 + retries):
            try:
                raw = _extract_json(text)
                return PhaseResult(**raw).model_dump()
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt >= retries:
                    raise exc
                logger.warning(
                    "JSON parse failed",
                    extra={
                        "event": "phase.json_parse_failed",
                        "attempt": attempt + 1,
                        "max_attempts": 1 + retries,
                        "error": str(exc),
                    },
                )
                text = await self._request_json_repair(text)
        raise RuntimeError("all parse attempts exhausted")

    async def _request_json_repair(self, broken_text: str) -> str:
        """Ask the LLM to fix malformed JSON without re-running the phase."""
        repair_prompt = JSON_REPAIR_PROMPT.format(broken_text=broken_text)
        llm = build_llm(model=self._blueprint._resolve_llm_config(self))
        agent = Agent(llm=llm, tools=[], system_prompt=BASE_SYSTEM_PROMPT, critic=None)
        conversation = LocalConversation(agent=agent, workspace=LocalWorkspace(working_dir="."))
        conversation.send_message(repair_prompt)
        await conversation.arun()
        return get_agent_final_response(conversation.state.events) or broken_text


class LoadProjectPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.path_template: str = raw["load_project"]
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self.depends_on: list[str] | None = raw.get("depends_on")
        self._blueprint = blueprint

    async def run(self, context: dict) -> dict:
        path = self.path_template.format(**context)
        self._blueprint._load_project(path)
        return {"success": True, "status": LoadProjectStatus.LOADED, "path": path}
