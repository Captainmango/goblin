from __future__ import annotations

from goblin.utils.config import get_config
from notifications_relay import RelayClient
from notifications_relay.local_store import resolve_token


def make_client() -> RelayClient:
    """Create a ``RelayClient`` from Goblin environment variables."""
    conf = get_config()
    host = conf.relay_host
    port = conf.relay_port
    api_key = conf.relay_api_key
    if not api_key:
        raise ValueError("RELAY_API_KEY is not set")
    return RelayClient(base_url=f"http://{host}:{port}", api_key=api_key)


def resolve(label: str | None = None, token: str | None = None) -> str:
    """Resolve a Telegram pairing token using envvar or local store."""
    return resolve_token(label=label, token=token)
