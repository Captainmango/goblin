from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml


def resolve_imports(
    raw: dict,
    base_dir: str,
    root_dir: str,
    visited: set[str] | None = None,
    resolved: dict[str, dict] | None = None,
) -> dict:
    """Resolve and merge imported blueprints into the current blueprint.

    Parameters
    ----------
    raw:
        The raw blueprint dict loaded from YAML (contains ``version`` and
        ``blueprint`` keys).
    base_dir:
        Directory against which relative import paths are resolved.
    root_dir:
        Root blueprint directory used for sandboxing — no resolved import may
        escape this tree.
    visited:
        Canonical paths currently on the recursion stack (for cycle detection).
    resolved:
        Cache of canonical path -> fully resolved blueprint dict (for diamond
        import deduplication).

    Returns
    -------
    A new blueprint dict with all imports recursively resolved and merged.
    """
    if visited is None:
        visited = set()
    if resolved is None:
        resolved = {}

    result = copy.deepcopy(raw)
    blueprint = result.get("blueprint", {})
    imports = blueprint.get("imports", [])
    if not imports:
        return result

    root_version = result.get("version")
    merged_blueprint = copy.deepcopy(blueprint)
    merged_blueprint.pop("imports", None)

    for imp in imports:
        path = imp["path"]
        prefix = imp.get("as")

        resolved_path = _resolve_path(path, base_dir, root_dir)
        canonical_path = str(Path(resolved_path).resolve())

        if canonical_path in visited:
            raise ValueError(f"Import cycle detected involving '{path}'")

        if canonical_path in resolved:
            imported_resolved = resolved[canonical_path]
        else:
            imported_raw = yaml.safe_load(Path(resolved_path).read_text())

            if imported_raw is None:
                raise ValueError(f"Imported blueprint '{path}' is empty or invalid")

            imported_version = imported_raw.get("version")
            if imported_version != root_version:
                raise ValueError(
                    f"Version mismatch in import '{path}': expected '{root_version}', got '{imported_version}'"
                )

            imported_dir = str(Path(resolved_path).resolve().parent)
            visited_copy = visited | {canonical_path}
            imported_resolved = resolve_imports(imported_raw, imported_dir, root_dir, visited_copy, resolved)
            resolved[canonical_path] = imported_resolved

        imported_spec = imported_resolved.get("blueprint", {})
        merged_blueprint = _merge_blueprints(merged_blueprint, imported_spec, prefix, path)

    result["blueprint"] = merged_blueprint
    return result


def _resolve_path(path: str, base_dir: str, root_dir: str) -> str:
    """Resolve an import path relative to *base_dir*, sandboxed to *root_dir*.

    Raises
    ------
    ValueError
        For absolute paths, ``..`` traversal, or symlink escapes.
    FileNotFoundError
        When the resolved file does not exist.
    """
    base = Path(base_dir)
    root = Path(root_dir)
    target = base / path

    if Path(path).is_absolute():
        raise ValueError(f"Absolute import paths are not allowed: '{path}'")

    # Reject literal .. components before normalization
    if ".." in path.split("/"):
        raise ValueError(f"Import paths cannot traverse outside the blueprint directory: '{path}'")

    resolved = target.resolve()
    canonical_root = root.resolve()

    # Ensure the canonical path is inside the root tree
    try:
        resolved.relative_to(canonical_root)
    except ValueError as exc:
        raise ValueError(f"Import path escapes the blueprint directory: '{path}'") from exc

    if not resolved.is_file():
        raise FileNotFoundError(f"Imported blueprint not found: '{path}' (resolved to '{resolved}')")

    return str(resolved)


def _merge_blueprints(parent: dict, imported: dict, prefix: str | None, import_path: str) -> dict:
    """Merge *imported* blueprint into *parent*.  Parent wins on conflicts."""
    result = copy.deepcopy(parent)

    # vars: shallow merge, parent wins
    imported_vars = imported.get("vars", {})
    parent_vars = result.get("vars", {})
    merged_vars = {**imported_vars, **parent_vars}
    if merged_vars:
        result["vars"] = merged_vars

    # defaults: deep merge, parent wins at each level
    imported_defaults = imported.get("defaults", {})
    parent_defaults = result.get("defaults", {})
    merged_defaults = _deep_merge(imported_defaults, parent_defaults)
    if merged_defaults:
        result["defaults"] = merged_defaults

    # workspace: parent completely replaces imported
    imported_workspace = imported.get("workspace")
    parent_workspace = result.get("workspace")
    if parent_workspace is not None:
        result["workspace"] = parent_workspace
    elif imported_workspace is not None:
        result["workspace"] = copy.deepcopy(imported_workspace)
    elif "workspace" in result:
        del result["workspace"]

    # phases: concatenate, imported first
    raw_imported_phases = imported.get("phases", [])
    parent_phases = result.get("phases", [])

    if prefix:
        imported_phases = [_prefix_phase(p, prefix) for p in raw_imported_phases]
    else:
        parent_ids = {p["id"] for p in parent_phases}
        for p in raw_imported_phases:
            if p["id"] in parent_ids:
                raise ValueError(f"Duplicate phase ID '{p['id']}' from import '{import_path}' without 'as' prefix")
        imported_phases = copy.deepcopy(raw_imported_phases)

    # Use original imported phase IDs (before prefixing) for condition rewriting
    original_imported_ids = [p["id"] for p in raw_imported_phases]
    imported_phases = [_rewrite_phase_references(p, prefix, original_imported_ids) for p in imported_phases]

    result["phases"] = imported_phases + parent_phases
    return result


def _prefix_phase(phase: dict, prefix: str) -> dict:
    """Prefix the phase ID with the given namespace."""
    result = copy.deepcopy(phase)
    result["id"] = f"{prefix}.{phase['id']}"
    return result


def _rewrite_phase_references(phase: dict, prefix: str | None, imported_phase_ids: list[str]) -> dict:
    """Rewrite ``depends_on``, ``history``, and ``condition`` references."""
    if not prefix:
        return phase

    result = copy.deepcopy(phase)

    if "depends_on" in result:
        result["depends_on"] = [f"{prefix}.{dep}" for dep in result["depends_on"]]

    if "history" in result:
        result["history"] = [f"{prefix}.{h}" for h in result["history"]]

    if "condition" in result:
        result["condition"] = _rewrite_condition(result["condition"], prefix, imported_phase_ids)

    return result


def _rewrite_condition(condition: str, prefix: str, imported_phase_ids: list[str]) -> str:
    """Rewrite ``steps.<id>`` references in a Jinja2 condition string."""
    # Sort by length descending so longer IDs match before shorter prefixes
    for phase_id in sorted(imported_phase_ids, key=len, reverse=True):
        pattern = rf"\bsteps\.{re.escape(phase_id)}(?!\w)"
        replacement = f"steps.{prefix}.{phase_id}"
        condition = re.sub(pattern, replacement, condition)
    return condition


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts.  *override* wins at every level."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
