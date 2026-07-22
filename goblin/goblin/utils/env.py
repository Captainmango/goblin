from __future__ import annotations

import os
import re
from typing import Any

from goblin.utils.tree import walk_strings

# Only braced ${VAR} syntax is supported.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Detect unbraced $VAR so we can alert the user with a helpful message.
_UNBRACED_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def validate_env_var_syntax(value):
    """Recursively check that env-var references use braced ${VAR} syntax.

    Raises ValueError if any unbraced $VAR pattern is found.
    """

    def _check(s: str) -> str:
        # Find unbraced $VAR references, but ignore already-braced ${VAR}
        # by checking the character before each match isn't '{'.
        for match in _UNBRACED_VAR_RE.finditer(s):
            start = match.start()
            if start > 0 and s[start - 1] == "{":
                continue  # part of ${VAR}, not $VAR
            var_name = match.group(1)
            raise ValueError(f"Invalid env-var syntax '${var_name}'. Use '${{{var_name}}}' (braced) syntax instead.")
        return s

    walk_strings(value, _check)


def resolve_env_vars(value) -> Any:
    """Recursively resolve ${VAR} references from os.environ.

    Raises ValueError if a referenced environment variable is not set.
    """

    def _resolve(s: str) -> str:
        def _replace(match):
            var_name = match.group(1)
            val = os.environ.get(var_name)
            if val is None:
                raise ValueError(f"Environment variable '{var_name}' is not set")
            return val

        return _ENV_VAR_RE.sub(_replace, s)

    return walk_strings(value, _resolve)


def collect_env_vars(value) -> set[str]:
    """Recursively collect all ``${VAR}`` references from strings in *value*."""
    names: set[str] = set()

    def _collect(s: str) -> str:
        names.update(_ENV_VAR_RE.findall(s))
        return s

    walk_strings(value, _collect)
    return names
