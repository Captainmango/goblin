from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol

from jinja2 import Environment

from claude_agent_sdk import (
    AgentDefinition,
    HookMatcher,
)
from goblin.hooks import HOOK_REGISTRY
from goblin.step import LoadProjectStep, PromptStep, StepProtocol
from goblin.utils.jinja_helpers import filter_contains, filter_json_value


class Blueprint(Protocol):
    async def run(self, context: dict) -> dict: ...


class V1Blueprint(Blueprint):
    def __init__(self, spec: dict):
        self.spec = spec
        self.project_path: str | None = None
        self.project_settings_path: str | None = None
        self.project_refs: str = ""
        self.project_agents: dict[str, AgentDefinition] = {}
        self.steps: list[StepProtocol] = [self._parse_step(s) for s in spec["steps"]]

    def _parse_step(self, raw: dict) -> StepProtocol:
        match raw:
            case {"load_project": str(), **_rest}:
                return LoadProjectStep(raw, self)
            case {"prompt": str(), **_rest}:
                return PromptStep(raw, self)
            case _:
                raise ValueError(f"Step '{raw.get('id', '?')}' must have either 'prompt' or 'load_project'")

    def _build_hooks(self, step: PromptStep) -> dict:
        defaults = self.spec.get("defaults", {}).get("hooks", {})
        step_hooks = step.hooks_config

        # Step-level hooks override defaults entirely if specified
        pre_names = step_hooks.get("pre_tool", defaults.get("pre_tool", []))
        post_names = step_hooks.get("post_tool", defaults.get("post_tool", []))

        return {
            "PreToolUse": [HookMatcher(hooks=[HOOK_REGISTRY[n] for n in pre_names if n in HOOK_REGISTRY])],
            "PostToolUse": [HookMatcher(hooks=[HOOK_REGISTRY[n] for n in post_names if n in HOOK_REGISTRY])],
        }

    def _evaluate_condition(self, condition: str, results: dict) -> bool:
        """Evaluate a Jinja2 condition expression against step results.

        Available in templates:
          - steps.<id>.success — boolean
          - steps.<id>.message — list of message strings

        Custom filters:
          - contains(text) — True if any message contains the substring
          - json_value(key) — parse messages as JSON dicts and return
            the value at a dot-separated key path (e.g. "data.status")
        """
        env = Environment()
        env.filters["contains"] = filter_contains
        env.filters["json_value"] = filter_json_value

        # Wrap the condition in {{ }} if it isn't already — allows bare
        # expressions like `steps.clone_repo.success` alongside the full
        # `{{ steps.clone_repo.success }}` syntax.
        expr = condition.strip()
        if not expr.startswith("{{"):
            expr = "{{ " + expr + " }}"

        template = env.from_string(expr)
        rendered = template.render(steps=results).strip()

        # Jinja renders booleans as "True"/"False"; treat common truthy/falsy values
        return rendered.lower() not in ("false", "none", "0", "")

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

    async def _handle_step_failure(self, step: StepProtocol, context: dict, results: dict) -> bool:
        """Handle a failed step. Returns True to continue the run, False to abort."""
        on_failure = step.on_failure or {}
        action = on_failure.get("action", "abort")
        notify_hook = on_failure.get("notify")

        print(f"Step {step.id} failed")

        if notify_hook and notify_hook in HOOK_REGISTRY:
            HOOK_REGISTRY[notify_hook](results[step.id])

        if action == "skip":
            print(f"Skipping failed step {step.id}, continuing workflow")
            return True

        if action == "retry":
            print(f"Retrying step {step.id}")
            result = await step.run(context)
            results[step.id] = result
            if result.get("success", True):
                return True
            print(f"Step {step.id} failed again after retry, aborting")
            return False

        # abort (default)
        print(f"Aborting workflow due to step {step.id} failure")
        return False

    async def run(self, context: dict = {}):
        # Merge blueprint vars with caller context; caller context wins on conflicts
        blueprint_vars = self.spec.get("vars", {})
        context = {**blueprint_vars, **context}

        results = {}
        for step in self.steps:
            # Check conditions
            if step.condition:
                if not self._evaluate_condition(step.condition, results):
                    print(f"Skipping {step.id} — condition not met")
                    continue

            # Handle foreach expansion
            if step.foreach:
                items = self._resolve(step.foreach, results)
                for item in items:
                    step_context = {**context, **item}
                    result = await step.run(step_context)
                    results[step.id] = result
                    if not result.get("success", True):
                        if not await self._handle_step_failure(step, step_context, results):
                            return results
            else:
                result = await step.run(context)
                results[step.id] = result
                if not result.get("success", True):
                    if not await self._handle_step_failure(step, context, results):
                        return results

        return results
