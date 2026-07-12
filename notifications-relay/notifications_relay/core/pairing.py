from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from notifications_relay.core.models import PairingResponse
from notifications_relay.exceptions import PairingExpiredError, PairingNotFoundError
from notifications_relay.store import Store


class PairingService:
    """Manages the lifecycle of pairing tokens."""

    def __init__(self, store: Store, token_ttl_seconds: int = 900) -> None:
        self.store = store
        self.token_ttl_seconds = token_ttl_seconds

    async def create(
        self,
        label: str | None = None,
        username: str | None = None,
    ) -> PairingResponse:
        """Create a pending pairing token."""
        token = secrets.token_urlsafe(32)
        await self.store.create_pairing(token, label, username, self.token_ttl_seconds)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.token_ttl_seconds)).isoformat()
        return PairingResponse(token=token, expires_at=expires_at)

    async def claim(
        self,
        token: str,
        telegram_user_id: str,
        chat_id: str,
    ) -> None:
        """Claim a pending token for a Telegram user/chat."""
        pairing = await self.store.get_pairing_by_token(token)
        if pairing is None:
            raise PairingNotFoundError(f"Unknown pairing token: {token}")
        if pairing["status"] != "pending":
            raise PairingNotFoundError(f"Pairing token is not pending: {token}")
        now = datetime.now(timezone.utc).isoformat()
        if pairing["expires_at"] < now:
            raise PairingExpiredError(f"Pairing token expired: {token}")
        await self.store.claim_pairing(token, telegram_user_id, chat_id)

    async def revoke(self, token: str) -> bool:
        """Revoke an active pairing."""
        pairing = await self.store.get_pairing_by_token(token)
        if pairing is None or pairing["status"] != "claimed":
            raise PairingNotFoundError(f"No active pairing for token: {token}")
        return await self.store.revoke_pairing(token)

    async def cleanup_expired(self) -> int:
        """Remove expired pending pairings."""
        return await self.store.delete_expired_pending()
