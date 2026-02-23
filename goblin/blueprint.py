from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    query,
)
from goblin.hooks import HOOK_REGISTRY


class Blueprint(Protocol):
    async def run(self, context: dict) -> dict: ...


class V1Blueprint:
    def __init__(self, spec: dict):
        self.spec = spec
        self.project_path: str | None = None
        self.project_settings_path: str | None = None
        self.project_refs: str = ""
        self.project_agents: dict[str, AgentDefinition] = {}

    def _build_hooks(self, step: dict) -> dict:
        defaults = self.spec.get("defaults", {}).get("hooks", {})
        step_hooks = step.get("hooks", {})

        # Step-level hooks override defaults entirely if specified
        pre_names = step_hooks.get("pre_tool", defaults.get("pre_tool", []))
        post_names = step_hooks.get("post_tool", defaults.get("post_tool", []))

        return {
            "PreToolUse": [HookMatcher(hooks=[HOOK_REGISTRY[n] for n in pre_names if n in HOOK_REGISTRY])],
            "PostToolUse": [HookMatcher(hooks=[HOOK_REGISTRY[n] for n in post_names if n in HOOK_REGISTRY])],
        }

    def _load_project(self, path: str):
        """Load full project configuration from a project root.

        Reads settings, CLAUDE.md references, and agent definitions from the
        project and stores them as instance state for subsequent prompt steps.

        - Settings: stores path to .claude/settings.json (passed to SDK via settings param)
        - References: reads CLAUDE.md and .claude/CLAUDE.md, concatenates into project_refs
        - Agents: scans .claude/agents/*.md, each becomes an AgentDefinition
        - Hooks: registers subprocess-backed hooks as 'project_pre_tool'/'project_post_tool'
        """
        self.project_path = path

        # Settings
        settings_path = os.path.join(path, ".claude", "settings.json")
        if os.path.isfile(settings_path):
            self.project_settings_path = settings_path
            print(f"Loaded project settings from {settings_path}")

            # Register subprocess-backed hooks from settings
            with open(settings_path) as f:
                settings = json.load(f)
            hooks_config = settings.get("hooks", {})
            for event_type, registry_name in (("PreToolUse", "project_pre_tool"), ("PostToolUse", "project_post_tool")):
                commands = [
                    hook_def["command"]
                    for matcher_def in hooks_config.get(event_type, [])
                    for hook_def in matcher_def.get("hooks", [])
                    if hook_def.get("type") == "command" and hook_def.get("command")
                ]
                if not commands:
                    continue

                def make_runner(cmds, cwd):
                    def run_project_hooks(tool_use):
                        for cmd in cmds:
                            result = subprocess.run(cmd, shell=True, cwd=cwd)
                            if result.returncode != 0:
                                print(f"Project hook failed (exit {result.returncode}): {cmd}")
                    return run_project_hooks

                HOOK_REGISTRY[registry_name] = make_runner(commands, path)
                print(f"Registered {len(commands)} {event_type} command(s) as '{registry_name}'")
        else:
            print(f"No .claude/settings.json found in {path}")

        # References (CLAUDE.md files)
        refs = []
        for ref_path in [
            os.path.join(path, "CLAUDE.md"),
            os.path.join(path, ".claude", "CLAUDE.md"),
        ]:
            if os.path.isfile(ref_path):
                with open(ref_path) as f:
                    refs.append(f.read())
                print(f"Loaded reference from {ref_path}")
        self.project_refs = "\n\n".join(refs)

        # Agent definitions
        agents_dir = os.path.join(path, ".claude", "agents")
        if os.path.isdir(agents_dir):
            for filename in os.listdir(agents_dir):
                if filename.endswith(".md"):
                    agent_name = filename.removesuffix(".md")
                    with open(os.path.join(agents_dir, filename)) as f:
                        content = f.read()
                    self.project_agents[agent_name] = AgentDefinition(
                        description=agent_name,
                        prompt=content,
                    )
                    print(f"Loaded agent definition: {agent_name}")

    async def run_step(self, step: dict, context: dict):
        prompt = step["prompt"].format(**context)

        kwargs: dict = {
            "allowed_tools": step.get("allowed_tools", []),
            "hooks": self._build_hooks(step),
            "env": {"CLAUDECODE": ""},
        }

        if self.project_path:
            kwargs["cwd"] = self.project_path
        if self.project_settings_path:
            kwargs["settings"] = self.project_settings_path
        if self.project_refs:
            kwargs["system_prompt"] = {
                "type": "preset",
                "preset": "claude_code",
                "append": self.project_refs,
            }
        if self.project_agents:
            kwargs["agents"] = self.project_agents

        options = ClaudeAgentOptions(**kwargs)

        result = []

        # Agentic loop: streams messages as Claude works
        async for message in query(
            prompt=prompt,
            options=options
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        print(block.text)
                        result.append(block.text)  # Claude's reasoning
                    elif hasattr(block, "name"):
                        result.append(f"Tool: {block.name}")  # Tool being called
            elif isinstance(message, ResultMessage):
                result.append(f"Done: {message.subtype}")

        return result

    async def run(self, context: dict = {}):
        results = {}
        for step in self.spec["steps"]:
            step_id = step["id"]

            # load_project steps import project config rather than running the agent
            if "load_project" in step:
                path = step["load_project"].format(**context)
                self._load_project(path)
                results[step_id] = {"status": "loaded", "path": path}
                continue

            # Check conditions
            if "condition" in step:
                if not self._evaluate_condition(step["condition"], results):
                    print(f"Skipping {step_id} — condition not met")
                    continue

            # Handle foreach expansion
            if "foreach" in step:
                items = self._resolve(step["foreach"], results)
                for item in items:
                    step_context = {**context, **item}
                    try:
                        results[step_id] = await self.run_step(step, step_context)
                    except Exception:
                        if step.get("on_failure", {}).get("action") == "skip":
                            print(f"Step {step_id} failed for {item}, skipping")
                            continue
                        raise
            else:
                results[step_id] = await self.run_step(step, context)

        return results
