from __future__ import annotations

import logging

from telegram import Bot

from notifications_relay.core.models import SendResponse
from notifications_relay.exceptions import PairingNotFoundError, SendError
from notifications_relay.store import Store
from notifications_relay.telegram.config import TelegramConfig

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096


class TelegramNotificationProvider:
    """Outbound Telegram provider using ``python-telegram-bot``."""

    def __init__(
        self,
        bot: Bot,
        store: Store,
        config: TelegramConfig,
        mapping_ttl_seconds: int = 3600,
    ) -> None:
        self.bot = bot
        self.store = store
        self.config = config
        self.mapping_ttl_seconds = mapping_ttl_seconds

    @property
    def name(self) -> str:
        return "telegram"

    @staticmethod
    def _truncate(text: str, limit: int = MAX_MESSAGE_LENGTH) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    async def send(
        self,
        token: str,
        text: str,
        question_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResponse:
        pairing = await self.store.get_pairing_by_token(token)
        if pairing is None or pairing["status"] != "claimed":
            logger.warning(
                "telegram_pairing_not_found",
                extra={"token_prefix": token[:8] if token else None},
            )
            raise PairingNotFoundError(f"No active Telegram pairing for token: {token}")

        chat_id = int(pairing["chat_id"])
        truncated = self._truncate(text)

        try:
            message = await self.bot.send_message(chat_id=chat_id, text=truncated)
        except Exception as exc:
            logger.warning(
                "telegram_send_failed",
                extra={"token": token, "error": str(exc)},
            )
            raise SendError(f"Telegram send failed: {exc}") from exc

        if question_id and message is not None:
            await self.store.create_question_mapping(
                chat_id=str(message.chat.id),
                telegram_message_id=str(message.message_id),
                token=token,
                question_id=question_id,
                ttl_seconds=self.mapping_ttl_seconds,
            )

        return SendResponse(message_id=message.message_id, channel=self.name)
