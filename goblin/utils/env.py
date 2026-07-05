from __future__ import annotations

import os
import re
from typing import Any

# Only braced ${VAR} syntax is supported for MCP server config.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Detect unbraced $VAR so we can alert the user with a helpful message.
_UNBRACED_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def validate_env_var_syntax(value):
    """Recursively check that env-var references use braced ${VAR} syntax.

    Raises ValueError if any unbraced $VAR pattern is found.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            validate_env_var_syntax(v)
        return
    if isinstance(value, list):
        for item in value:
            validate_env_var_syntax(item)
        return
    if isinstance(value, str):
        # Find unbraced $VAR references, but ignore already-braced ${VAR}
        # by checking the character before each match isn't '{'.
        for match in _UNBRACED_VAR_RE.finditer(value):
            start = match.start()
            if start > 0 and value[start - 1] == "{":
                continue  # part of ${VAR}, not $VAR
            var_name = match.group(1)
            raise ValueError(
                f"Invalid env-var syntax '${var_name}' in MCP server config. "
                f"Use '${{{var_name}}}' (braced) syntax instead."
            )
    return


def resolve_env_vars(value) -> Any:
    """Recursively resolve ${VAR} references from os.environ.

    Raises ValueError if a referenced environment variable is not set.
    """
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    if isinstance(value, str):

        def _replace(match):
            var_name = match.group(1)
            val = os.environ.get(var_name)
            if val is None:
                raise ValueError(f"Environment variable '{var_name}' is not set (referenced in MCP server config)")
            return val

        return _ENV_VAR_RE.sub(_replace, value)
    return value
