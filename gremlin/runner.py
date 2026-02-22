# runner.py
from claude_agent_sdk import ResultMessage
from claude_agent_sdk import AssistantMessage
from claude_agent_sdk import query
import json
import os
import subprocess
import yaml
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
from gremlin.hooks import HOOK_REGISTRY

class WorkflowRunner:
    def __init__(self, workflow_path: str):
        with open(workflow_path) as f:
            self.spec = yaml.safe_load(f)["workflow"]

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

    def _load_hooks_from(self, path: str):
        """Read .claude/settings.json from a project root and register its shell hooks.

        Hook commands are wrapped as subprocess callables and stored in HOOK_REGISTRY
        under two reserved names: 'project_pre_tool' and 'project_post_tool'.
        The project's .claude/settings.json follows the Claude Code hooks format:

            {
              "hooks": {
                "PostToolUse": [
                  { "matcher": "Write|Edit", "hooks": [{ "type": "command", "command": "npm test" }] }
                ]
              }
            }
        """
        settings_path = os.path.join(path, ".claude", "settings.json")
        if not os.path.isfile(settings_path):
            print(f"No .claude/settings.json found in {path}, skipping hook load")
            return

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

    async def run_step(self, step: dict, context: dict):
        prompt = step["prompt"].format(**context)
        options = ClaudeAgentOptions(
            allowed_tools=step.get("allowed_tools", []),
            hooks=self._build_hooks(step),
            env={"CLAUDECODE": ""},
        )

        result = []

        # Agentic loop: streams messages as Claude works
        async for message in query(
            prompt=prompt,
            options=options
        ):
            print(message)
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

            # load_hooks steps import project hook files rather than running the agent
            if "load_hooks" in step:
                path = step["load_hooks"].format(**context)
                self._load_hooks_from(path)
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