from __future__ import annotations

import os
from pathlib import Path
from typing import Any, overload

from pydantic import SecretStr


@overload
def env(name: str, default: None = None, *, fallback: str | None = None) -> str | None: ...


@overload
def env(name: str, default: bool, *, fallback: str | None = None) -> bool: ...


@overload
def env(name: str, default: str, *, fallback: str | None = None) -> str: ...


@overload
def env(name: str, default: int, *, fallback: str | None = None) -> int: ...


def env(name: str, default: Any = None, *, fallback: str | None = None) -> Any:
    """Read an environment variable and coerce it to the type of ``default``.

    The value is read from ``name`` first, then from ``fallback`` if provided.
    If neither is set, ``default`` is returned. When a value is present, it is
    coerced to the type of ``default`` (int, bool, etc.). String defaults keep
    the raw string value.
    """
    value = os.environ.get(name)
    if value is None and fallback is not None:
        value = os.environ.get(fallback)
    if value is None:
        return default
    if default is None or isinstance(default, str):
        return value
    if isinstance(default, bool):
        return value.lower() in ("true", "1", "yes", "on")
    try:
        return type(default)(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Cannot coerce environment variable {name}={value!r} to {type(default).__name__}") from exc


def secret_env(name: str, *, fallback: str | None = None) -> SecretStr | None:
    """Read a sensitive environment variable as a pydantic ``SecretStr``."""
    value = os.environ.get(name)
    if value is None and fallback is not None:
        value = os.environ.get(fallback)
    return SecretStr(value) if value else None


def load_dotenv_from_cwd() -> None:
    """Load a ``.env`` file from the current working directory only.

    Falls back to a no-op when ``python-dotenv`` is not installed.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(dotenv_path=Path.cwd() / ".env")
