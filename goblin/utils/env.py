from __future__ import annotations

import os
import re

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def resolve_env_vars(value):
    """Recursively resolve $VAR and ${VAR} references from os.environ.

    Raises ValueError if a referenced environment variable is not set.
    """
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    if isinstance(value, str):

        def _replace(match):
            var_name = match.group(1) or match.group(2)
            val = os.environ.get(var_name)
            if val is None:
                raise ValueError(f"Environment variable '{var_name}' is not set (referenced in MCP server config)")
            return val

        return _ENV_VAR_RE.sub(_replace, value)
    return value
