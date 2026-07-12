from __future__ import annotations

from typing import Protocol, runtime_checkable

from notifications_relay.core.models import SendResponse


@runtime_checkable
class NotificationProvider(Protocol):
    """Protocol for outbound notification providers."""

    @property
    def name(self) -> str:
        """Provider channel name, e.g. ``telegram``."""
        ...

    async def send(
        self,
        token: str,
        text: str,
        question_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResponse:
        """Send ``text`` to the recipient bound to ``token``."""
        ...
