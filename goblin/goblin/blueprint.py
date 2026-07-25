from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from openhands.sdk.hooks import HookConfig
    from openhands.sdk.subagent import AgentDefinition

    from goblin.checkpoint import Checkpoint
    from goblin.phase import PhaseProtocol

logger = logging.getLogger(__name__)


class HookablePhase(Protocol):
    hooks_config: dict
    mcp_servers_config: dict | None


class Blueprint(Protocol):
    async def run(self, context: dict | None = None, checkpoint: Checkpoint | None = None) -> dict: ...


class V1Blueprint(Blueprint):
    def __init__(self, spec: dict):
        from goblin.dependency_graph import DependencyGraph
        from goblin.utils.env import resolve_env_vars, validate_env_var_syntax

        self.spec = spec
        self.project_path: str | None = None
        self.project_refs: str = ""
        self.project_agents: dict[str, AgentDefinition] = {}

        # Resolve blueprint-level workspace config (env vars + interpolation)
        raw_workspace = spec.get("workspace")
        self._workspace_config: dict | None = None
        if raw_workspace is not None:
            validate_env_var_syntax(raw_workspace)
            self._workspace_config = cast(dict, resolve_env_vars(raw_workspace))

        self.phases: list[PhaseProtocol] = [self._parse_phase(s) for s in spec["phases"]]
        self._dependency_graph = DependencyGraph(self.phases)

    def _parse_phase(self, raw: dict) -> PhaseProtocol:
        from goblin.phase import LoadProjectPhase, PromptPhase

        match raw:
            case {"load_project": str(), **_rest}:
                return LoadProjectPhase(raw, self)
            case {"prompt": str(), **_rest}:
                return PromptPhase(raw, self)
            case {"swarm": dict(), **_rest}:
                from goblin.swarm import SwarmPhase

                return SwarmPhase(raw, self)
            case _:
                raise ValueError(f"Phase '{raw.get('id', '?')}' must have 'prompt', 'load_project', or 'swarm'")

    def build_history_context(self, history_ids: list[str], results: dict) -> str:
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

    def resolve_llm_config(self, phase: PhaseProtocol, context: dict | None = None) -> dict | str | None:
        """Return the effective OpenHands LLM configuration for a phase.

        Precedence: phase.llm > phase.model > defaults.llm > defaults.model > None.
        Dict configs have environment variables and {variable} context interpolation
        resolved before being returned. String values are returned as-is so that
        build_llm can fall back to environment-based credentials.
        """
        from goblin.utils.env import resolve_env_vars, validate_env_var_syntax

        defaults = self.spec.get("defaults", {})
        default_llm = defaults.get("llm")
        default_model = defaults.get("model")

        phase_llm = getattr(phase, "llm_config", None)
        phase_model = getattr(phase, "model", None)

        if phase_llm is not None:
            selected = phase_llm
        elif phase_model is not None:
            selected = phase_model
        elif default_llm is not None:
            selected = default_llm
        elif default_model is not None:
            selected = default_model
        else:
            selected = None

        if isinstance(selected, dict):
            validate_env_var_syntax(selected)
            resolved = resolve_env_vars(selected)
            return self.interpolate_strings(resolved, context or {})

        return selected

    def resolve_model(self, phase: PhaseProtocol, context: dict | None = None) -> str | None:
        """Return the effective model identifier for a phase.

        Precedence: phase.llm.model > phase.model > defaults.llm.model > defaults.model > None.
        """
        config = self.resolve_llm_config(phase, context)
        if isinstance(config, dict):
            return config.get("model")
        return config

    def resolve_worktree_config(self, phase: PhaseProtocol) -> dict | None:
        """Return the effective worktree config for a phase.

        Precedence: phase > blueprint defaults > None. Returns None when no
        worktree configuration is declared anywhere.
        """
        defaults = self.spec.get("defaults", {}).get("worktree")
        phase_config = getattr(phase, "worktree_config", None)
        if defaults is None and phase_config is None:
            return None
        merged = {**(defaults or {}), **(phase_config or {})}
        return merged

    def build_hooks(self, phase: HookablePhase) -> HookConfig | None:
        from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher

        from goblin.hooks import HOOK_REGISTRY

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

    def build_mcp_servers(self, phase: HookablePhase, context: dict) -> dict:
        from goblin.utils.env import collect_env_vars, resolve_env_vars, validate_env_var_syntax

        defaults = self.spec.get("defaults", {}).get("mcp_servers", {})
        phase_servers = phase.mcp_servers_config or {}
        merged = {**defaults, **phase_servers}
        if not merged:
            return {}
        validate_env_var_syntax(merged)

        # Collect explicitly referenced ${VAR}s before interpolation so we can
        # forward only those values (plus a small allowlist) to stdio MCP servers.
        referenced_vars = collect_env_vars(merged)
        resolved = resolve_env_vars(merged)
        interpolated = cast(dict, self.interpolate_strings(resolved, context))

        # Forward a conservative allowlist plus any env vars the blueprint explicitly
        # references. This prevents third-party MCP servers from inheriting goblin's
        # own secrets (LLM keys, Telegram tokens, Turso auth, etc.) by default.
        allowed_base_vars = {
            "HOME",
            "USER",
            "LOGNAME",
            "USERNAME",
            "PATH",
            "SHELL",
            "LANG",
            "LC_ALL",
            "PWD",
            "TMPDIR",
            "TERM",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
        }
        base_env = {name: os.environ[name] for name in allowed_base_vars if name in os.environ}
        referenced_env = {name: os.environ[name] for name in referenced_vars if name in os.environ}
        forwarded = {**base_env, **referenced_env}

        for server_config in interpolated.values():
            if isinstance(server_config, dict) and "command" in server_config:
                explicit_env = server_config.get("env", {})
                server_config["env"] = {**forwarded, **explicit_env}

        return {"mcpServers": interpolated}

    def interpolate_strings(self, obj: Any, context: dict) -> Any:
        """Recursively apply {variable} interpolation to all string values."""
        from goblin.utils.tree import walk_strings

        return walk_strings(obj, lambda s: s.format(**context))

    def get_workspace(self, context: dict):
        """Return the workspace for this blueprint, applying context interpolation.

        The workspace is built lazily so that {variable} interpolation can use
        the runtime context (including vars from load_project and prior phases).
        """
        from goblin.workspace import build_workspace

        interpolated = self.interpolate_strings(self._workspace_config or {}, context)
        return build_workspace(interpolated if interpolated else None, self.project_path)

    def _jinja_env(self):
        from jinja2 import Environment

        from goblin.utils.jinja_helpers import filter_contains, filter_json_value

        env = Environment()
        filters = cast(dict[str, Any], env.filters)
        filters["contains"] = filter_contains
        filters["json_value"] = filter_json_value
        return env

    def _resolve_foreach_count(self, n: int) -> list[dict]:
        if n < 0:
            raise ValueError(f"foreach count must be non-negative, got {n}")
        return [{"index": i} for i in range(n)]

    def resolve_foreach(self, foreach_expr: str, results: dict, context: dict) -> list[dict]:
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

        path = Path(interpolated)
        if not path.is_absolute() and self.project_path:
            path = Path(self.project_path) / path
        path = path.resolve()

        lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]

        return [{"index": i, "item": line} for i, line in enumerate(lines)]

    def evaluate_condition(self, condition: str, results: dict) -> bool:
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

    def load_project(self, path: str):
        """Load full project configuration from a project root.

        Reads settings, CLAUDE.md references, and agent definitions from the
        project and stores them as instance state for subsequent prompt phases.

        - Settings: reads .claude/settings.json and registers shell command hooks
        - References: reads CLAUDE.md and .claude/CLAUDE.md, concatenates into project_refs
        - Agents: scans .claude/agents/*.md, each becomes an AgentDefinition
        """
        from openhands.sdk.hooks import HookDefinition
        from openhands.sdk.subagent import AgentDefinition

        from goblin.hooks import register_hook

        self.project_path = path

        project_dir = Path(path)
        settings_path = project_dir / ".claude" / "settings.json"
        if settings_path.is_file():
            logger.info(
                "Loaded project settings",
                extra={"event": "project.settings_loaded", "path": str(settings_path)},
            )

            settings = json.loads(settings_path.read_text())
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
            project_dir / "CLAUDE.md",
            project_dir / ".claude" / "CLAUDE.md",
        ]:
            if ref_path.is_file():
                refs.append(ref_path.read_text())
                logger.info("Loaded reference", extra={"event": "project.reference_loaded", "path": str(ref_path)})
        self.project_refs = "\n\n".join(refs)

        agents_dir = project_dir / ".claude" / "agents"
        if agents_dir.is_dir():
            for agent_file in agents_dir.iterdir():
                if agent_file.suffix == ".md":
                    agent_name = agent_file.stem
                    content = agent_file.read_text()
                    self.project_agents[agent_name] = AgentDefinition(
                        name=agent_name,
                        system_prompt=content,
                    )
                    logger.info("Loaded agent definition", extra={"event": "project.agent_loaded", "agent": agent_name})

    async def run(self, context: dict | None = None, checkpoint: Checkpoint | None = None):
        from goblin.orchestrator import PhaseOrchestrator

        blueprint_vars = self.spec.get("vars", {})
        context = {**blueprint_vars, **(context or {})}
        orchestrator = PhaseOrchestrator(self)
        return await orchestrator.run(self.phases, context, checkpoint)

    # Backwards-compatible aliases for code/tests that still use the old
    # underscore-prefixed names.
    _build_history_context = build_history_context
    _resolve_llm_config = resolve_llm_config
    _resolve_model = resolve_model
    _resolve_worktree_config = resolve_worktree_config
    _build_hooks = build_hooks
    _build_mcp_servers = build_mcp_servers
    _interpolate_strings = interpolate_strings
    _get_workspace = get_workspace
    _evaluate_condition = evaluate_condition
    _resolve = resolve_foreach
    _load_project = load_project
