from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator, ValidationError

_cached_schema: dict[str, Any] | None = None


def load_schema() -> dict[str, Any]:
    """Load and cache the blueprint JSON Schema from the package data.

    The schema is shipped as package data (``goblin/spec/v1.json``) so that
    installed wheels can validate blueprints without requiring the repository
    root to be the current working directory.
    """
    global _cached_schema
    if _cached_schema is not None:
        return _cached_schema

    schema_bytes = resources.files("goblin").joinpath("spec/v1.json").read_bytes()
    _cached_schema = json.loads(schema_bytes)
    return _cached_schema


def validate_blueprint(path: str, raw: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Load a blueprint YAML file and validate it against the JSON Schema plus semantic rules.

    If *raw* is provided it is used directly (imports are assumed to already
    be resolved).  Otherwise the file at *path* is loaded and imports are
    resolved inline before schema validation.

    Returns a list of structured finding dicts, each with:
      - path: JSON path to the offending field (e.g. "blueprint.phases[0].id")
      - message: human-readable error message
      - validator: the schema keyword or "semantic"
      - severity: "error" or "warning"
    """
    if raw is None:
        raw = yaml.safe_load(Path(path).read_text())

        if raw is None:
            return [
                {
                    "path": "",
                    "message": "File is empty or not valid YAML",
                    "validator": "parse",
                    "severity": "error",
                }
            ]

        from goblin.import_resolver import resolve_imports

        base_dir = str(Path(path).resolve().parent)
        raw = resolve_imports(raw, base_dir, base_dir)

    validator = Draft7Validator(load_schema())
    errors: list[dict[str, Any]] = []

    for err in validator.iter_errors(raw):
        errors.append(
            {
                "path": _error_path(err),
                "message": err.message,
                "validator": err.validator,
                "severity": "error",
            }
        )

    errors.extend(_validate_output_model_refs(raw))
    errors.extend(_validate_hook_names(raw))
    errors.extend(_validate_acp_semantics(raw))
    return errors


def _validate_hook_names(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Check that every referenced hook name exists in ``HOOK_REGISTRY``."""
    from goblin.hooks import HOOK_REGISTRY

    blueprint = raw.get("blueprint", raw)
    if not isinstance(blueprint, dict):
        return []

    errors: list[dict[str, Any]] = []
    defaults = blueprint.get("defaults", {})
    default_hooks = defaults.get("hooks", {}) if isinstance(defaults, dict) else {}

    def _collect_hook_names(hooks: Any) -> list[tuple[str, str]]:
        if not isinstance(hooks, dict):
            return []
        names: list[tuple[str, str]] = []
        for source in ("pre_tool", "post_tool"):
            for name in hooks.get(source, []) or []:
                names.append((name, source))
        return names

    for name, source in _collect_hook_names(default_hooks):
        if name not in HOOK_REGISTRY:
            errors.append(
                {
                    "path": f"blueprint.defaults.hooks.{source}",
                    "message": f"Unknown hook name '{name}'",
                    "validator": "hook_name",
                    "severity": "error",
                }
            )

    for idx, phase in enumerate(blueprint.get("phases", [])):
        if not isinstance(phase, dict):
            continue
        phase_hooks = phase.get("hooks", {})
        for name, source in _collect_hook_names(phase_hooks):
            if name not in HOOK_REGISTRY:
                errors.append(
                    {
                        "path": f"blueprint.phases[{idx}].hooks.{source}",
                        "message": f"Unknown hook name '{name}'",
                        "validator": "hook_name",
                        "severity": "error",
                    }
                )
        on_failure = phase.get("on_failure", {})
        if isinstance(on_failure, dict):
            notify = on_failure.get("notify")
            if notify and notify not in HOOK_REGISTRY:
                errors.append(
                    {
                        "path": f"blueprint.phases[{idx}].on_failure.notify",
                        "message": f"Unknown hook name '{notify}'",
                        "validator": "hook_name",
                        "severity": "error",
                    }
                )

    return errors


def _validate_output_model_refs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Check that every ``output_model`` string resolves to a Pydantic model."""
    from goblin.output_schema import resolve_output_model_by_ref

    blueprint = raw.get("blueprint", raw)
    phases = blueprint.get("phases", []) if isinstance(blueprint, dict) else []
    errors: list[dict[str, Any]] = []
    for idx, phase in enumerate(phases):
        if not isinstance(phase, dict):
            continue
        output_model = phase.get("output_model")
        if not output_model:
            continue
        try:
            resolve_output_model_by_ref(str(output_model))
        except (ValueError, ImportError) as exc:
            errors.append(
                {
                    "path": f"blueprint.phases[{idx}].output_model",
                    "message": f"output_model {output_model!r} cannot be resolved: {exc}",
                    "validator": "output_model_resolution",
                    "severity": "error",
                }
            )
    return errors


def _validate_acp_semantics(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Check conditional ACP harness rules that JSON Schema cannot express."""
    blueprint = raw.get("blueprint", raw)
    if not isinstance(blueprint, dict):
        return []

    defaults = blueprint.get("defaults", {})
    default_harness = defaults.get("harness") if isinstance(defaults, dict) else None
    default_hooks = defaults.get("hooks") if isinstance(defaults, dict) else None
    default_worktree = defaults.get("worktree") if isinstance(defaults, dict) else None
    workspace = blueprint.get("workspace", {})
    workspace_type = workspace.get("type") if isinstance(workspace, dict) else None

    findings: list[dict[str, Any]] = []
    acp_phase_indices: list[int] = []

    for idx, phase in enumerate(blueprint.get("phases", [])):
        if not isinstance(phase, dict):
            continue

        # Only prompt phases carry harness semantics; other phase kinds already
        # reject harness at the schema level via additionalProperties.
        if "prompt" not in phase:
            continue

        phase_harness = phase.get("harness")
        merged_harness = {**(default_harness or {}), **(phase_harness or {})}
        harness_type = merged_harness.get("type", "openhands")
        if harness_type != "acp":
            continue

        acp_phase_indices.append(idx)
        phase_id = phase.get("id", f"<{idx}>")

        # Rule 1: ACP + phase allowed_tools.
        allowed_tools = phase.get("allowed_tools")
        if allowed_tools:
            findings.append(
                {
                    "path": f"blueprint.phases[{idx}].allowed_tools",
                    "message": (
                        f"Phase '{phase_id}' uses harness.type: acp; allowed_tools is not "
                        "supported because the ACP harness owns its own tools"
                    ),
                    "validator": "semantic",
                    "severity": "error",
                }
            )

        # Rule 2: ACP + phase-level hooks.
        phase_hooks = phase.get("hooks")
        if phase_hooks:
            findings.append(
                {
                    "path": f"blueprint.phases[{idx}].hooks",
                    "message": (
                        f"Phase '{phase_id}' uses harness.type: acp; hooks are not "
                        "supported because OpenHands tool hooks do not fire inside an ACP harness"
                    ),
                    "validator": "semantic",
                    "severity": "error",
                }
            )

        # Rule 3: ACP + worktree.
        phase_worktree = phase.get("worktree")
        merged_worktree = {**(default_worktree or {}), **(phase_worktree or {})}
        if merged_worktree:
            findings.append(
                {
                    "path": f"blueprint.phases[{idx}].worktree",
                    "message": (
                        f"Phase '{phase_id}' uses harness.type: acp; worktree is an "
                        "OpenHands tool and is not supported by ACP harnesses"
                    ),
                    "validator": "semantic",
                    "severity": "error",
                }
            )

        # Rule 6: custom server requires command.
        server = merged_harness.get("server", "claude-code")
        command = merged_harness.get("command")
        if server == "custom" and not command:
            findings.append(
                {
                    "path": f"blueprint.phases[{idx}].harness.command",
                    "message": (
                        f"Phase '{phase_id}' uses harness.server: custom, which requires harness.command to be set"
                    ),
                    "validator": "semantic",
                    "severity": "error",
                }
            )

        # Rule 7: command with non-custom server is a warning.
        if server != "custom" and command:
            findings.append(
                {
                    "path": f"blueprint.phases[{idx}].harness.command",
                    "message": (
                        f"Phase '{phase_id}' overrides the pinned {server} command; ensure this is intentional"
                    ),
                    "validator": "semantic",
                    "severity": "warning",
                }
            )

        # Rule 8: ACP + max_iterations is a warning.
        if phase.get("max_iterations") is not None:
            findings.append(
                {
                    "path": f"blueprint.phases[{idx}].max_iterations",
                    "message": (
                        f"Phase '{phase_id}' uses harness.type: acp; max_iterations is "
                        "ignored because an ACP phase is one prompt turn (use "
                        "harness.prompt_timeout as the guard)"
                    ),
                    "validator": "semantic",
                    "severity": "warning",
                }
            )

    # Rule 2: defaults-level hooks + any ACP phase.
    if default_hooks and acp_phase_indices:
        findings.append(
            {
                "path": "blueprint.defaults.hooks",
                "message": (
                    "defaults.hooks is set and at least one phase uses harness.type: acp; "
                    "OpenHooks tool hooks do not fire inside an ACP harness"
                ),
                "validator": "semantic",
                "severity": "error",
            }
        )

    # Rule 4: remote workspace + any ACP phase.
    if workspace_type == "remote" and acp_phase_indices:
        findings.append(
            {
                "path": "blueprint.workspace.type",
                "message": (
                    "workspace.type: remote cannot be combined with harness.type: acp; ACP requires a local subprocess"
                ),
                "validator": "semantic",
                "severity": "error",
            }
        )

    return findings


def _error_path(error: ValidationError) -> str:
    """Build a dotted JSON-path string from a jsonschema ValidationError."""
    parts: list[str] = []
    for elem in error.absolute_path:
        if isinstance(elem, int):
            parts.append(f"[{elem}]")
        else:
            if parts and not parts[-1].endswith("]"):
                parts.append(f".{elem}")
            else:
                parts.append(str(elem))
    return "".join(parts) if parts else "<root>"


def format_errors(errors: list[dict[str, Any]]) -> str:
    """Pretty-print validation findings for CLI output.

    Warnings are labelled but do not cause ``goblin validate`` to exit non-zero.
    """
    lines: list[str] = []
    for err in errors:
        path = err.get("path") or "<root>"
        message = err.get("message", "Unknown error")
        severity = err.get("severity", "error")
        prefix = f"  - {path}:"
        if severity == "warning":
            prefix = f"  - {path} (warning):"
        lines.append(f"{prefix} {message}")
    return "\n".join(lines) if lines else ""
