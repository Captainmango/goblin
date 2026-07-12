from __future__ import annotations

from typing import Any, Callable


def walk_strings(value: Any, fn: Callable[[str], Any]) -> Any:
    """Recursively apply *fn* to every string leaf in *value*.

    Dicts and lists are rebuilt with the same shape (dict keys are left
    untouched — only values are walked); any other type (int, bool, None,
    float, ...) passes through unchanged. Used to share the dict/list/str
    traversal across env-var resolution, env-var syntax validation, and
    {variable} interpolation, which otherwise each hand-roll the same
    recursion with only their string-leaf behavior differing.
    """
    if isinstance(value, dict):
        return {k: walk_strings(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [walk_strings(v, fn) for v in value]
    if isinstance(value, str):
        return fn(value)
    return value
