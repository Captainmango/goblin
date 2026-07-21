from __future__ import annotations

import os
from dataclasses import field
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic.dataclasses import dataclass

from goblin_common.config import env, load_dotenv_from_cwd, secret_env


@dataclass(frozen=True)
class RelayConfig:
    """Central runtime configuration for the notifications relay.

    Fields are loaded from environment variables when no explicit value is
    provided. Use :func:`load_config` for a fresh instance or
    :func:`get_config` for the module-level singleton.
    """

    api_key: SecretStr | None = field(default_factory=lambda: secret_env("RELAY_API_KEY"))

    def require_api_key(self) -> SecretStr:
        """Return the API key, raising if it is not configured."""
        if self.api_key is None:
            raise RuntimeError("RELAY_API_KEY environment variable is not set")
        return self.api_key

    host: str = field(default_factory=lambda: env("RELAY_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: env("RELAY_PORT", 8000))
    db_path: Path = field(default_factory=lambda: Path(env("RELAY_DB_PATH", ".goblin/relay.db")))
    pairing_token_ttl_seconds: int = field(default_factory=lambda: env("RELAY_PAIRING_TOKEN_TTL_SECONDS", 900))
    question_mapping_ttl_seconds: int = field(default_factory=lambda: env("RELAY_QUESTION_MAPPING_TTL_SECONDS", 3600))

    # Logging
    log_config: str | None = field(default_factory=lambda: env("RELAY_LOG_CONFIG"))
    log_level: str | None = field(default_factory=lambda: env("RELAY_LOG_LEVEL"))
    log_file: str | None = field(default_factory=lambda: env("RELAY_LOG_FILE"))
    log_quiet: bool = field(default_factory=lambda: env("RELAY_LOG_QUIET", False))

    @field_validator("db_path", mode="before")
    @classmethod
    def _coerce_db_path(cls, value: str | os.PathLike[str]) -> Path:
        return Path(value)


_config: RelayConfig | None = None


def load_config() -> RelayConfig:
    """Create a fresh config instance from the current environment."""
    load_dotenv_from_cwd()
    return RelayConfig()


def get_config() -> RelayConfig:
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
