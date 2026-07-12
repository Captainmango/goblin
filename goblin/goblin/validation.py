from __future__ import annotations

import json
import os
from typing import Any

import yaml
from jsonschema import Draft7Validator, ValidationError


SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "../..", "spec", "v1.json")


_cached_schema: dict[str, Any] | None = None


def load_schema() -> dict[str, Any]:
    """Load and cache the blueprint JSON Schema."""
    global _cached_schema
    if _cached_schema is not None:
        return _cached_schema

    path = SCHEMA_PATH
    if not os.path.isfile(path):
        # Fallback to cwd for cases where the package is installed differently
        path = os.path.join(os.getcwd(), "spec", "v1.json")

    with open(path) as f:
        _cached_schema = json.load(f)

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
        with open(path) as f:
            raw = yaml.safe_load(f)

        if raw is None:
            return [{"path": "", "message": "File is empty or not valid YAML", "validator": "parse"}]

        from goblin.import_resolver import resolve_imports

        raw = resolve_imports(raw, os.path.dirname(path), os.path.dirname(path))

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
