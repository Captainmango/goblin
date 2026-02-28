from __future__ import annotations

import json
import re
from typing import Dict, Protocol, TYPE_CHECKING

from pydantic import BaseModel

from goblin.prompts import BASE_SYSTEM_PROMPT

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    query,
)


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
        self.depends_on: str | list[str] | None = raw.get("depends_on")
        self.on_failure: dict | None = raw.get("on_failure")
        self._blueprint = blueprint

    async def run(self, context: dict) -> list:
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

        options = ClaudeAgentOptions(**kwargs)

        text_blocks: list[str] = []

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        text_blocks.append(block.text)

        raw = _extract_json(text_blocks[-1])
        return StepResult(**raw).model_dump()


class LoadProjectStep(StepProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        self.id: str = raw["id"]
        self.path_template: str = raw["load_project"]
        self.condition: str | None = raw.get("condition")
        self.foreach: str | None = raw.get("foreach")
        self.depends_on: str | list[str] | None = raw.get("depends_on")
        self.on_failure: dict | None = raw.get("on_failure")
        self._blueprint = blueprint

    async def run(self, context: dict) -> dict:
        path = self.path_template.format(**context)
        self._blueprint._load_project(path)
        return {"success": True, "status": "loaded", "path": path}
