from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr


def _env(name: str, default: Any = None, *, fallback: str | None = None) -> Any:
    value = os.environ.get(name)
    if value is not None:
        return value
    if fallback is not None:
        return os.environ.get(fallback, default)
    return default


def _secret_env(name: str, *, fallback: str | None = None) -> SecretStr | None:
    value = os.environ.get(name)
    if value is None and fallback is not None:
        value = os.environ.get(fallback)
    return SecretStr(value) if value else None


@dataclass(frozen=True)
class GoblinConfig:
    """Central configuration loaded from environment variables.

    Each field reads from a specific environment variable when no explicit
    value is provided. Instances can be constructed directly for testing or
    created via :func:`load_config` / :func:`get_config` to pull values from
    the current process environment.
    """

    # OpenHands SDK behaviour
    openhands_suppress_banner: str = field(default_factory=lambda: _env("OPENHANDS_SUPPRESS_BANNER", "1"))

    # Swarm backend selection
    backend: str | None = field(default_factory=lambda: _env("GOBLIN_BACKEND"))

    # Turso / libSQL configuration
    turso_url: str | None = field(default_factory=lambda: _env("GOBLIN_TURSO_URL"))
    turso_mode: str | None = field(default_factory=lambda: _env("GOBLIN_TURSO_MODE"))
    turso_auth_token: SecretStr | None = field(default_factory=lambda: _secret_env("GOBLIN_TURSO_AUTH_TOKEN"))
    turso_local_path: str | None = field(default_factory=lambda: _env("GOBLIN_TURSO_LOCAL_PATH"))

    # Logging
    log_config: str | None = field(default_factory=lambda: _env("GOBLIN_LOG_CONFIG"))

    # LLM API credentials
    model_api_key: SecretStr | None = field(
        default_factory=lambda: _secret_env("GOBLIN_MODEL_API_KEY", fallback="MODEL_API_KEY")
    )
    base_url: str | None = field(default_factory=lambda: _env("GOBLIN_BASE_URL", fallback="OPENHANDS_BASE_URL"))

    def require_model_api_key(self) -> SecretStr:
        """Return the model API key, raising if it is not configured."""
        if self.model_api_key is None:
            raise RuntimeError("GOBLIN_MODEL_API_KEY environment variable is not set")
        return self.model_api_key


_config: GoblinConfig | None = None


def load_config() -> GoblinConfig:
    """Create a fresh config instance from the current environment."""
    return GoblinConfig()


def get_config() -> GoblinConfig:
    """Return the module-level config singleton.

    The singleton is lazily loaded the first time this function is called so
    that tests can monkeypatch environment variables before the config is
    resolved.
    """
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Reset the module-level config singleton.

    Useful in tests that need to re-evaluate environment variables.
    """
    global _config
    _config = None
