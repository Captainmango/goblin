from __future__ import annotations

from notifications_relay.core.models import SendRequest, SendResponse
from notifications_relay.exceptions import (
    PairingNotFoundError,
    ProviderNotRegisteredError,
    SendError,
)
from notifications_relay.provider import NotificationProvider
from notifications_relay.store import Store


class NotificationDispatcher:
    """Routes outbound send requests to the correct channel provider."""

    def __init__(self, store: Store, mapping_ttl_seconds: int = 3600) -> None:
        self.store = store
        self.mapping_ttl_seconds = mapping_ttl_seconds
        self._providers: dict[str, NotificationProvider] = {}

    def register(self, name: str, provider: NotificationProvider) -> None:
        """Register a notification provider under ``name``."""
        self._providers[name] = provider

    @property
    def telegram_registered(self) -> bool:
        """Whether the Telegram provider is available."""
        return "telegram" in self._providers

    async def send(self, request: SendRequest) -> SendResponse:
        """Dispatch a send request to the provider bound to the token."""
        pairing = await self.store.get_pairing_by_token(request.token)
        if pairing is None or pairing["status"] != "claimed":
            raise PairingNotFoundError(f"No active pairing for token: {request.token}")

        provider = self._providers.get("telegram")
        if provider is None:
            raise ProviderNotRegisteredError("Telegram provider is not registered")

        if request.idempotency_key:
            cached = await self.store.get_idempotency_response(request.idempotency_key)
            if cached is not None:
                return SendResponse(**cached)

        try:
            response = await provider.send(
                request.token,
                request.text,
                question_id=request.question_id,
                idempotency_key=request.idempotency_key,
            )
        except Exception as exc:
            raise SendError(f"Failed to send message: {exc}") from exc

        if request.question_id:
            await self.store.create_question_mapping(
                chat_id=pairing["chat_id"],
                telegram_message_id=str(response.message_id),
                token=request.token,
                question_id=request.question_id,
                ttl_seconds=self.mapping_ttl_seconds,
            )

        if request.idempotency_key:
            await self.store.set_idempotency_response(
                request.idempotency_key,
                response.model_dump(),
            )

        return response
