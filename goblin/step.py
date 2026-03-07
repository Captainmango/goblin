from __future__ import annotations

import json
import logging
import re
from typing import Dict, Protocol, TYPE_CHECKING

from pydantic import BaseModel

from goblin.prompts import BASE_SYSTEM_PROMPT, JSON_REPAIR_PROMPT

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    query,
)

logger = logging.getLogger(__name__)


MAX_PARSE_RETRIES = 3


class StepResult(BaseModel):
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


class StepProtocol(Protocol):
    id: str
    condition: str | None
    foreach: str | None
    on_failure: dict | None

    async def run(self, context: dict) -> Dict: ...


class PromptStep(StepProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.prompt_template: str = raw["prompt"]
        self.allowed_tools: list[str] = raw.get("allowed_tools", [])
        self.hooks_config: dict = raw.get("hooks", {})
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self.mcp_servers_config: dict | None = raw.get("mcp_servers")
        self._blueprint = blueprint

    async def run(self, context: dict) -> Dict:
        prompt = self.prompt_template.format(**context)

        kwargs: dict = {
            "system_prompt": BASE_SYSTEM_PROMPT,
            "allowed_tools": self.allowed_tools,
            "hooks": self._blueprint._build_hooks(self),
            "env": {"CLAUDECODE": ""},
        }

        if self._blueprint.project_path:
            kwargs["cwd"] = self._blueprint.project_path
        if self._blueprint.project_settings_path:
            kwargs["settings"] = self._blueprint.project_settings_path

        if self._blueprint.project_agents:
            kwargs["agents"] = self._blueprint.project_agents

        mcp_servers = self._blueprint._build_mcp_servers(self, context)
        if mcp_servers:
            kwargs["mcp_servers"] = mcp_servers

        options = ClaudeAgentOptions(**kwargs)

        text_blocks: list[str] = []

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        text_blocks.append(block.text)

        if not text_blocks:
            return {"success": False, "message": ["Agent produced no text output"]}

        return await self._parse_response(text_blocks[-1])

    async def _parse_response(self, text: str, retries: int = MAX_PARSE_RETRIES) -> Dict:
        """Parse agent response JSON, retrying with a repair prompt on failure."""
        for attempt in range(1 + retries):
            try:
                raw = _extract_json(text)
                return StepResult(**raw).model_dump()
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt >= retries:
                    raise exc
                logger.warning(
                    "JSON parse failed",
                    extra={
                        "event": "step.json_parse_failed",
                        "attempt": attempt + 1,
                        "max_attempts": 1 + retries,
                        "error": str(exc),
                    },
                )
                # This variable is used. Each loop iter uses the previous run's text value
                text = await self._request_json_repair(text)
        raise RuntimeError("all parse attempts exhausted")

    async def _request_json_repair(self, broken_text: str) -> str:
        """Ask the LLM to fix malformed JSON without re-running the step."""
        repair_prompt = JSON_REPAIR_PROMPT.format(broken_text=broken_text)
        options = ClaudeAgentOptions(
            system_prompt=BASE_SYSTEM_PROMPT,
            allowed_tools=[],
            env={"CLAUDECODE": ""},
        )

        text_blocks: list[str] = []
        async for message in query(prompt=repair_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        text_blocks.append(block.text)

        return text_blocks[-1]


class LoadProjectStep(StepProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.path_template: str = raw["load_project"]
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.on_failure: dict | None = raw.get("on_failure")
        self._blueprint = blueprint

    async def run(self, context: dict) -> dict:
        path = self.path_template.format(**context)
        self._blueprint._load_project(path)
        return {"success": True, "status": "loaded", "path": path}
