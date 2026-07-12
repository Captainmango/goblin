from __future__ import annotations

import os

from notifications_relay import RelayClient
from notifications_relay.local_store import resolve_token


def make_client() -> RelayClient:
    """Create a ``RelayClient`` from Goblin environment variables."""
    host = os.environ.get("RELAY_HOST", "127.0.0.1")
    port = int(os.environ.get("RELAY_PORT", "8000"))
    api_key = os.environ.get("RELAY_API_KEY", "")
    if not api_key:
        raise ValueError("RELAY_API_KEY is not set")
    return RelayClient(base_url=f"http://{host}:{port}", api_key=api_key)


def resolve(label: str | None = None, token: str | None = None) -> str:
    """Resolve a Telegram pairing token using envvar or local store."""
    return resolve_token(label=label, token=token)
