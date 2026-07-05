from __future__ import annotations

import json
import logging
import re
from typing import Dict, Protocol, TYPE_CHECKING

from pydantic import BaseModel

import asyncio

from openhands.sdk import Agent, LocalConversation, LocalWorkspace, RemoteConversation, RemoteWorkspace
from openhands.sdk.conversation import get_agent_final_response
from goblin.cost_control import CircuitBreaker, LimitExceeded
from goblin.enums import LoadProjectStatus
from goblin.prompts import BASE_SYSTEM_PROMPT, JSON_REPAIR_PROMPT
from goblin.utils.llm import build_llm
from goblin.utils.tool_map import build_tool_filter_regex, resolve_tools

logger = logging.getLogger(__name__)


MAX_PARSE_RETRIES = 3


class PhaseResult(BaseModel):
    success: bool
    message: list


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
        self.depends_on: list[str] | None = raw.get("depends_on")
        self._blueprint = blueprint

    async def run(self, context: dict) -> Dict:
        prompt = self.prompt_template.format(**context)

        history_context = context.get("_history_context", "")
        if history_context:
            prompt = history_context + prompt

        system_prompt = BASE_SYSTEM_PROMPT
        if self._blueprint.project_refs:
            system_prompt = system_prompt + "\n\n" + self._blueprint.project_refs

        breaker = CircuitBreaker(
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            max_cost=self.max_cost,
        )

        llm = build_llm(model=self._blueprint._resolve_model(self))
        agent = Agent(
            llm=llm,
            tools=resolve_tools(self.allowed_tools),
            mcp_config=self._blueprint._build_mcp_servers(self, context),
            system_prompt=system_prompt,
            filter_tools_regex=build_tool_filter_regex(self.allowed_tools),
            critic=None,
        )

        workspace = self._blueprint._get_workspace(context)

        if isinstance(workspace, RemoteWorkspace):
            conversation = RemoteConversation(
                agent=agent,
                workspace=workspace,
                hook_config=self._blueprint._build_hooks(self),
                max_iteration_per_run=self.max_iterations or 500,
                max_budget_per_run=self.max_cost,
            )
            conversation.send_message(prompt)
            try:
                await asyncio.to_thread(conversation.run)
                breaker.absorb(conversation)
            except LimitExceeded as exc:
                return {
                    "success": False,
                    "message": [f"Circuit breaker: {exc.reason} limit exceeded"],
                    "prompt": prompt,
                    "output": "",
                    "spend": exc.tracker,
                }
        else:
            conversation = LocalConversation(
                agent=agent,
                workspace=workspace,
                hook_config=self._blueprint._build_hooks(self),
                max_iteration_per_run=self.max_iterations or 500,
                max_budget_per_run=self.max_cost,
            )
            conversation.send_message(prompt)
            try:
                await conversation.arun()
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
        llm = build_llm(model=self._blueprint._resolve_model(self))
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
