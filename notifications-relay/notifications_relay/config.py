from __future__ import annotations

import os
from dataclasses import field
from pathlib import Path
from typing import Any

from pydantic import SecretStr, field_validator
from pydantic.dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(*args: Any, **kwargs: Any) -> None:  # type: ignore[no-redef]
        """No-op fallback when python-dotenv is not installed."""
        pass


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
class RelayConfig:
    """Central runtime configuration for the notifications relay.

    Fields are loaded from environment variables when no explicit value is
    provided. Use :func:`load_config` for a fresh instance or
    :func:`get_config` for the module-level singleton.
    """

    api_key: SecretStr = field(default_factory=lambda: _secret_env("RELAY_API_KEY", fallback="fake-secret"))

    host: str = field(default_factory=lambda: _env("RELAY_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env("RELAY_PORT", 8000))
    db_path: Path = field(default_factory=lambda: Path(_env("RELAY_DB_PATH", ".goblin/relay.db")))
    pairing_token_ttl_seconds: int = field(default_factory=lambda: _env("RELAY_PAIRING_TOKEN_TTL_SECONDS", 900))
    question_mapping_ttl_seconds: int = field(default_factory=lambda: _env("RELAY_QUESTION_MAPPING_TTL_SECONDS", 3600))

    @field_validator("db_path", mode="before")
    @classmethod
    def _coerce_db_path(cls, value: str | os.PathLike[str]) -> Path:
        return Path(value)


_config: RelayConfig | None = None


def load_config() -> RelayConfig:
    """Create a fresh config instance from the current environment."""
    load_dotenv()
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
