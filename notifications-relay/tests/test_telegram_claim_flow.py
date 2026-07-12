from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notifications_relay.core.pairing import PairingService
from notifications_relay.store import Store
from notifications_relay.telegram.config import TelegramConfig
from notifications_relay.telegram.handlers import pair_handler


def _make_update(user_id: int = 123, chat_id: int = 123) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(id=chat_id)
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context(token: str) -> MagicMock:
    context = MagicMock()
    context.args = [token]
    return context


@pytest.mark.asyncio
async def test_pair_handler_claims_token(store: Store) -> None:
    pairing_service = PairingService(store)
    pairing = await pairing_service.create(label="alice")
    config = TelegramConfig(bot_token="dummy", allowed_user_ids=set())

    update = _make_update()
    context = _make_context(pairing.token)

    await pair_handler(update, context, pairing_service, config)

    update.message.reply_text.assert_awaited_once()
    assert "complete" in update.message.reply_text.await_args[0][0]

    claimed = await store.get_pairing_by_token(pairing.token)
    assert claimed["status"] == "claimed"
    assert claimed["telegram_user_id"] == "123"


@pytest.mark.asyncio
async def test_pair_handler_rejects_unknown_token(store: Store) -> None:
    pairing_service = PairingService(store)
    config = TelegramConfig(bot_token="dummy", allowed_user_ids=set())

    update = _make_update()
    context = _make_context("unknown-token")

    await pair_handler(update, context, pairing_service, config)

    update.message.reply_text.assert_awaited_once()
    assert "Invalid" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_pair_handler_respects_allow_list(store: Store) -> None:
    pairing_service = PairingService(store)
    pairing = await pairing_service.create(label="alice")
    config = TelegramConfig(bot_token="dummy", allowed_user_ids={999})

    update = _make_update(user_id=123)
    context = _make_context(pairing.token)

    await pair_handler(update, context, pairing_service, config)

    update.message.reply_text.assert_awaited_once()
    assert "not authorized" in update.message.reply_text.await_args[0][0]
