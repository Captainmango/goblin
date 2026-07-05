from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol, cast

from jinja2 import Environment

from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.subagent import AgentDefinition
from goblin.checkpoint import Checkpoint
from goblin.enums import FailureAction
from goblin.hooks import HOOK_REGISTRY, register_hook
from goblin.utils.env import resolve_env_vars
from goblin.phase import LoadProjectPhase, PromptPhase, PhaseProtocol
from goblin.swarm import SwarmPhase
from goblin.utils.jinja_helpers import filter_contains, filter_json_value

logger = logging.getLogger(__name__)


class HookablePhase(Protocol):
    hooks_config: dict
    mcp_servers_config: dict | None


class Blueprint(Protocol):
    async def run(self, context: dict | None = None, checkpoint: Checkpoint | None = None) -> dict: ...


class V1Blueprint(Blueprint):
    def __init__(self, spec: dict):
        self.spec = spec
        self.project_path: str | None = None
        self.project_refs: str = ""
        self.project_agents: dict[str, AgentDefinition] = {}
        self.phases: list[PhaseProtocol] = [self._parse_phase(s) for s in spec["phases"]]
        self._validate_history()

    def _parse_phase(self, raw: dict) -> PhaseProtocol:
        match raw:
            case {"load_project": str(), **_rest}:
                return LoadProjectPhase(raw, self)
            case {"prompt": str(), **_rest}:
                return PromptPhase(raw, self)
            case {"swarm": dict(), **_rest}:
                return SwarmPhase(raw, self)
            case _:
                raise ValueError(f"Phase '{raw.get('id', '?')}' must have 'prompt', 'load_project', or 'swarm'")

    def _validate_history(self):
        """Validate history references at parse time."""
        phase_ids_seen: list[str] = []
        foreach_phase_ids: set[str] = set()

        for phase in self.phases:
            history = getattr(phase, "history", None)
            if history:
                if not isinstance(phase, PromptPhase):
                    raise ValueError(f"Phase '{phase.id}': history is only valid on prompt phases")
                for ref_id in history:
                    if ref_id not in phase_ids_seen:
                        raise ValueError(
                            f"Phase '{phase.id}': history references '{ref_id}' which is not defined before this phase"
                        )
                    if ref_id in foreach_phase_ids:
                        raise ValueError(f"Phase '{phase.id}': history cannot reference foreach phase '{ref_id}'")
                    referenced = next(s for s in self.phases if s.id == ref_id)
                    if not isinstance(referenced, PromptPhase):
                        raise ValueError(
                            f"Phase '{phase.id}': history can only reference prompt "
                            f"phases, but '{ref_id}' is a {type(referenced).__name__}"
                        )

            phase_ids_seen.append(phase.id)
            if phase.foreach:
                foreach_phase_ids.add(phase.id)

    def _build_history_context(self, history_ids: list[str], results: dict) -> str:
        """Build a context string from prior phases' prompts and outputs."""
        sections = []
        for phase_id in history_ids:
            if phase_id not in results:
                continue
            phase_result = results[phase_id]
            phase_prompt = phase_result.get("prompt", "")
            output = phase_result.get("output", "")
            if not output and not phase_prompt:
                continue
            section = f"### Phase: {phase_id}\n"
            if phase_prompt:
                section += f"\n**Prompt:** {phase_prompt}\n"
            if output:
                section += f"\n**Output:**\n{output}\n"
            sections.append(section)

        if not sections:
            return ""

        return "## Prior Phase Results\n\n" + "\n".join(sections) + "\n---\n\n"

    def _build_hooks(self, phase: HookablePhase) -> HookConfig | None:
        defaults = self.spec.get("defaults", {}).get("hooks", {})
        phase_hooks = phase.hooks_config

        blueprint_merge = defaults.get("merge", False)
        merge = phase_hooks.get("merge", blueprint_merge)

        default_pre = defaults.get("pre_tool") or []
        default_post = defaults.get("post_tool") or []
        phase_pre = phase_hooks.get("pre_tool") or []
        phase_post = phase_hooks.get("post_tool") or []

        if merge:
            pre_names = [*default_pre, *phase_pre]
            post_names = [*default_post, *phase_post]
        else:
            pre_names = phase_pre if phase_hooks.get("pre_tool") is not None else default_pre
            post_names = phase_post if phase_hooks.get("post_tool") is not None else default_post

        pre_defs = [
            HOOK_REGISTRY[n] for n in pre_names if n in HOOK_REGISTRY and isinstance(HOOK_REGISTRY[n], HookDefinition)
        ]
        post_defs = [
            HOOK_REGISTRY[n] for n in post_names if n in HOOK_REGISTRY and isinstance(HOOK_REGISTRY[n], HookDefinition)
        ]

        if not pre_defs and not post_defs:
            return None

        return HookConfig(
            pre_tool_use=[HookMatcher(hooks=pre_defs)] if pre_defs else [],
            post_tool_use=[HookMatcher(hooks=post_defs)] if post_defs else [],
        )

    def _build_mcp_servers(self, phase: HookablePhase, context: dict) -> dict:
        defaults = self.spec.get("defaults", {}).get("mcp_servers", {})
        phase_servers = phase.mcp_servers_config or {}
        merged = {**defaults, **phase_servers}
        if not merged:
            return {}
        resolved = resolve_env_vars(merged)
        interpolated = cast(dict, self._interpolate_strings(resolved, context))

        # The mcp library only forwards a minimal env (HOME, PATH, etc.) to stdio
        # subprocesses. Inject the full parent environment so MCP servers see the same
        # tokens as goblin — matching OpenHands' behavior. Blueprint env: keys win.
        parent_env = dict(os.environ)
        for server_config in interpolated.values():
            if isinstance(server_config, dict) and "command" in server_config:
                explicit_env = server_config.get("env", {})
                server_config["env"] = {**parent_env, **explicit_env}

        return {"mcpServers": interpolated}

    def _interpolate_strings(self, obj: Any, context: dict) -> Any:
        """Recursively apply {variable} interpolation to all string values."""
        if isinstance(obj, dict):
            return {k: self._interpolate_strings(v, context) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._interpolate_strings(item, context) for item in obj]
        if isinstance(obj, str):
            return obj.format(**context)
        return obj

    def _jinja_env(self) -> Environment:
        env = Environment()
        filters = cast(dict[str, Any], env.filters)
        filters["contains"] = filter_contains
        filters["json_value"] = filter_json_value
        return env

    def _resolve_foreach_count(self, n: int) -> list[dict]:
        if n < 0:
            raise ValueError(f"foreach count must be non-negative, got {n}")
        return [{"index": i} for i in range(n)]

    def _resolve(self, foreach_expr: str, results: dict, context: dict) -> list[dict]:
        if "{{" in foreach_expr:
            env = self._jinja_env()
            expr = foreach_expr.strip()
            if not expr.startswith("{{"):
                expr = "{{ " + expr + " }}"
            template = env.from_string(expr)
            rendered = template.render(steps=results).strip()
            try:
                n = int(rendered)
            except ValueError:
                raise ValueError(f"foreach Jinja expression rendered to '{rendered}', which is not a valid integer")
            return self._resolve_foreach_count(n)

        interpolated = foreach_expr.format(**context)
        try:
            n = int(interpolated)
            return self._resolve_foreach_count(n)
        except ValueError:
            pass

        path = interpolated
        if not os.path.isabs(path) and self.project_path:
            path = os.path.join(self.project_path, path)
        path = os.path.abspath(path)

        with open(path) as f:
            lines = [line.strip() for line in f if line.strip()]

        return [{"index": i, "item": line} for i, line in enumerate(lines)]

    def _evaluate_condition(self, condition: str, results: dict) -> bool:
        """Evaluate a Jinja2 condition expression against phase results.

        Available in templates:
          - steps.<id>.success — boolean
          - steps.<id>.message — list of message strings

        Custom filters:
          - contains(text) — True if any message contains the substring
          - json_value(key) — parse messages as JSON dicts and return
            the value at a dot-separated key path (e.g. "data.status")
        """
        env = self._jinja_env()

        expr = condition.strip()
        if not expr.startswith("{{"):
            expr = "{{ " + expr + " }}"

        template = env.from_string(expr)
        rendered = template.render(steps=results).strip()

        return rendered.lower() not in ("false", "none", "0", "")

    def _load_project(self, path: str):
        """Load full project configuration from a project root.

        Reads settings, CLAUDE.md references, and agent definitions from the
        project and stores them as instance state for subsequent prompt phases.

        - Settings: reads .claude/settings.json and registers shell command hooks
        - References: reads CLAUDE.md and .claude/CLAUDE.md, concatenates into project_refs
        - Agents: scans .claude/agents/*.md, each becomes an AgentDefinition
        """
        self.project_path = path

        settings_path = os.path.join(path, ".claude", "settings.json")
        if os.path.isfile(settings_path):
            logger.info("Loaded project settings", extra={"event": "project.settings_loaded", "path": settings_path})

            with open(settings_path) as f:
                settings = json.load(f)
            hooks_config = settings.get("hooks", {})
            for event_key, registry_name in (
                ("PreToolUse", "project_pre_tool"),
                ("PostToolUse", "project_post_tool"),
            ):
                commands = [
                    hook_def["command"]
                    for matcher_def in hooks_config.get(event_key, [])
                    for hook_def in matcher_def.get("hooks", [])
                    if hook_def.get("type") == "command" and hook_def.get("command")
                ]
                if not commands:
                    continue

                register_hook(registry_name, HookDefinition(command="; ".join(commands)))
                logger.info(
                    "Registered project hooks",
                    extra={
                        "event": "project.hooks_registered",
                        "count": len(commands),
                        "event_key": event_key,
                        "registry_name": registry_name,
                    },
                )
        else:
            logger.warning("No .claude/settings.json found", extra={"event": "project.settings_missing", "path": path})

        refs = []
        for ref_path in [
            os.path.join(path, "CLAUDE.md"),
            os.path.join(path, ".claude", "CLAUDE.md"),
        ]:
            if os.path.isfile(ref_path):
                with open(ref_path) as f:
                    refs.append(f.read())
                logger.info("Loaded reference", extra={"event": "project.reference_loaded", "path": ref_path})
        self.project_refs = "\n\n".join(refs)

        agents_dir = os.path.join(path, ".claude", "agents")
        if os.path.isdir(agents_dir):
            for filename in os.listdir(agents_dir):
                if filename.endswith(".md"):
                    agent_name = filename.removesuffix(".md")
                    with open(os.path.join(agents_dir, filename)) as f:
                        content = f.read()
                    self.project_agents[agent_name] = AgentDefinition(
                        name=agent_name,
                        system_prompt=content,
                    )
                    logger.info("Loaded agent definition", extra={"event": "project.agent_loaded", "agent": agent_name})

    async def _handle_phase_failure(self, phase: PhaseProtocol, context: dict, results: dict) -> bool:
        """Handle a failed phase. Returns True to continue the run, False to abort."""
        on_failure = phase.on_failure or {}
        action = FailureAction(on_failure.get("action", FailureAction.ABORT))
        notify_hook = on_failure.get("notify")

        logger.warning("Phase failed", extra={"event": "phase.failed", "phase_id": phase.id})

        if notify_hook and notify_hook in HOOK_REGISTRY:
            hook_val = HOOK_REGISTRY[notify_hook]
            if callable(hook_val):
                hook_val(results[phase.id])

        if action == FailureAction.SKIP:
            logger.warning("Skipping failed phase", extra={"event": "phase.skip_on_failure", "phase_id": phase.id})
            return True

        if action == FailureAction.RETRY:
            max_retries = on_failure.get("max_retries", 1)
            for attempt in range(1, max_retries + 1):
                logger.warning(
                    "Retrying phase",
                    extra={
                        "event": "phase.retry",
                        "phase_id": phase.id,
                        "attempt": attempt,
                        "max_retries": max_retries,
                    },
                )
                result = await phase.run(context)
                results[phase.id] = result
                if result.get("success", True):
                    return True
            logger.error(
                "Phase failed after retries",
                extra={"event": "phase.retry_exhausted", "phase_id": phase.id, "max_retries": max_retries},
            )
            return False

        logger.error("Aborting blueprint", extra={"event": "phase.abort", "phase_id": phase.id})
        return False

    async def run(self, context: dict | None = None, checkpoint: Checkpoint | None = None):
        blueprint_vars = self.spec.get("vars", {})
        context = {**blueprint_vars, **(context or {})}

        if checkpoint:
            results = checkpoint.get_results()
        else:
            results = {}

        for phase in self.phases:
            if checkpoint and phase.id in checkpoint.completed_phase_ids():
                logger.info(
                    "Skipping phase — already completed",
                    extra={"event": "phase.checkpoint_skip", "phase_id": phase.id},
                )
                continue

            if phase.condition:
                if not self._evaluate_condition(phase.condition, results):
                    logger.info(
                        "Skipping phase — condition not met",
                        extra={"event": "phase.condition_skip", "phase_id": phase.id},
                    )
                    if checkpoint:
                        checkpoint.phase_skipped(phase.id)
                    continue

            if phase.foreach:
                items = self._resolve(phase.foreach, results, context)
                if checkpoint:
                    checkpoint.phase_started(phase.id, {"foreach": phase.foreach, "item_count": len(items)})
                iteration_results = []
                for item in items:
                    phase_context = {**context, **item}
                    if hasattr(phase, "history") and phase.history:
                        history_context = self._build_history_context(phase.history, results)
                        phase_context = {**phase_context, "_history_context": history_context}
                    result = await phase.run(phase_context)
                    iteration_results.append(result)
                    if not result.get("success", True):
                        if not await self._handle_phase_failure(phase, phase_context, results):
                            results[phase.id] = {
                                "iterations": iteration_results,
                                "success": False,
                            }
                            if checkpoint:
                                checkpoint.phase_failed(phase.id, results[phase.id])
                                checkpoint.mark_failed()
                            return results
                results[phase.id] = {
                    "iterations": iteration_results,
                    "success": all(r.get("success", True) for r in iteration_results),
                }
                if checkpoint:
                    if results[phase.id].get("success", True):
                        checkpoint.phase_completed(phase.id, results[phase.id])
                    else:
                        checkpoint.phase_failed(phase.id, results[phase.id])
            else:
                phase_context = context
                if hasattr(phase, "history") and phase.history:
                    history_context = self._build_history_context(phase.history, results)
                    phase_context = {**context, "_history_context": history_context}
                if checkpoint:
                    checkpoint.phase_started(
                        phase.id, {"context": {k: v for k, v in phase_context.items() if k != "_history_context"}}
                    )
                result = await phase.run(phase_context)
                results[phase.id] = result
                if not result.get("success", True):
                    if not await self._handle_phase_failure(phase, context, results):
                        if checkpoint:
                            checkpoint.phase_failed(phase.id, results[phase.id])
                            checkpoint.mark_failed()
                        return results
                    if checkpoint:
                        final = results[phase.id]
                        if final.get("success", True):
                            checkpoint.phase_completed(phase.id, final)
                        else:
                            checkpoint.phase_failed(phase.id, final)
                else:
                    if checkpoint:
                        checkpoint.phase_completed(phase.id, result)

        if checkpoint:
            checkpoint.mark_completed()

        return results
