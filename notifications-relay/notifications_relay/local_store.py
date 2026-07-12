from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = ".goblin/notifications.json"


def _config_path(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("NOTIFICATIONS_CONFIG_PATH", DEFAULT_CONFIG_PATH))


def load_tokens(path: str | Path | None = None) -> dict[str, Any]:
    """Load the local token store from disk."""
    config_path = _config_path(path)
    if not config_path.exists():
        return {"telegram": {}}
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return {"telegram": {}}
    if "telegram" not in data or not isinstance(data["telegram"], dict):
        data["telegram"] = {}
    return data


def save_tokens(data: dict[str, Any], path: str | Path | None = None) -> None:
    """Persist the token store with restrictive permissions."""
    config_path = _config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.chmod(temp_path, 0o600)
    temp_path.replace(config_path)


def save_token(
    label: str,
    token: str,
    path: str | Path | None = None,
) -> None:
    """Store a pairing token under ``telegram.<label>``."""
    data = load_tokens(path)
    data["telegram"][label] = token
    save_tokens(data, path)


def resolve_token(
    label: str | None = None,
    token: str | None = None,
    env_prefix: str = "TELEGRAM_TOKEN_",
    path: str | Path | None = None,
) -> str:
    """Resolve a pairing token from explicit value, envvar, or local store.

    Precedence:
    1. Explicit ``token`` argument.
    2. ``TELEGRAM_TOKEN_<LABEL>`` environment variable.
    3. ``.goblin/notifications.json`` entry for the label.
    """
    if token:
        return token

    if label:
        env_name = f"{env_prefix}{label.upper()}"
        env_token = os.environ.get(env_name)
        if env_token:
            return env_token

        data = load_tokens(path)
        stored = data.get("telegram", {}).get(label)
        if stored:
            return stored

    raise ValueError(
        "No pairing token found. Provide --token, --label with a stored token, or set the matching envvar."
    )
