from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from notifications_relay.config import RelayConfig
from notifications_relay.core.dispatcher import NotificationDispatcher
from notifications_relay.core.inbox import InboxService
from notifications_relay.core.pairing import PairingService
from notifications_relay.store import Store


async def require_api_key(request: Request) -> None:
    """Dependency that validates the ``Authorization: Bearer <api_key>`` header."""
    config: RelayConfig = request.app.state.config
    auth = request.headers.get("Authorization", "")
    prefix = "Bearer "
    api_key = config.require_api_key()
    if not auth.startswith(prefix) or not secrets.compare_digest(auth[len(prefix) :], api_key.get_secret_value()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_pairing_service(request: Request) -> PairingService:
    return request.app.state.pairing_service


def get_inbox_service(request: Request) -> InboxService:
    return request.app.state.inbox_service


def get_dispatcher(request: Request) -> NotificationDispatcher:
    return request.app.state.dispatcher
