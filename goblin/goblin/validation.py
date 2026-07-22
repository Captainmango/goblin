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
    """Load a blueprint YAML file and validate it against the JSON Schema.

    If *raw* is provided it is used directly (imports are assumed to already
    be resolved).  Otherwise the file at *path* is loaded and imports are
    resolved inline before schema validation.

    Returns a list of structured error dicts, each with:
      - path: JSON path to the offending field (e.g. "blueprint.phases[0].id")
      - message: human-readable error message
      - validator: the schema keyword that failed (e.g. "required", "type")
    """
    if raw is None:
        raw = yaml.safe_load(Path(path).read_text())

        if raw is None:
            return [{"path": "", "message": "File is empty or not valid YAML", "validator": "parse"}]

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
            }
        )

    errors.extend(_validate_output_model_refs(raw))
    errors.extend(_validate_hook_names(raw))
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
                }
            )
    return errors


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
    """Pretty-print validation errors for CLI output."""
    lines: list[str] = []
    for err in errors:
        path = err.get("path") or "<root>"
        message = err.get("message", "Unknown error")
        lines.append(f"  - {path}: {message}")
    return "\n".join(lines) if lines else ""
