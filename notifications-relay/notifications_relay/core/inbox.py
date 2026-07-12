from __future__ import annotations

from notifications_relay.core.models import InboxMessage
from notifications_relay.store import Store


class InboxService:
    """Consumes inbound replies from the store."""

    def __init__(self, store: Store, ttl_seconds: int = 3600) -> None:
        self.store = store
        self.ttl_seconds = ttl_seconds

    async def fetch(
        self,
        question_id: str | None = None,
        token: str | None = None,
    ) -> list[InboxMessage]:
        """Fetch and delete replies matching the supplied filters."""
        rows = await self.store.fetch_inbox(question_id=question_id, token=token)
        return [InboxMessage(**row) for row in rows]

    async def cleanup_expired(self) -> int:
        """Remove expired inbox entries."""
        return await self.store.delete_expired_inbox()
