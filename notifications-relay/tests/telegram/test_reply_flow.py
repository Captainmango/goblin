from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notifications_relay.store import Store
from notifications_relay.telegram.handlers import reply_handler


def _make_reply_update(
    user_id: int = 123,
    chat_id: int = 123,
    reply_to_message_id: int = 100,
    text: str = "Yes",
) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.effective_chat = MagicMock(id=chat_id)
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_to_message = MagicMock(message_id=reply_to_message_id)
    update.message.reply_text = AsyncMock()
    return update


@pytest.mark.asyncio
async def test_reply_handler_creates_inbox_entry(store: Store) -> None:
    await store.create_pairing("token-1", "alice", None, 3600)
    await store.claim_pairing("token-1", "123", "123")
    await store.create_question_mapping("123", "100", "token-1", "q-1", 3600)

    update = _make_reply_update()
    context = MagicMock()

    await reply_handler(update, context, store, 3600)

    messages = await store.fetch_inbox(question_id="q-1")
    assert len(messages) == 1
    assert messages[0]["text"] == "Yes"
    assert messages[0]["token"] == "token-1"
    assert messages[0]["question_id"] == "q-1"


@pytest.mark.asyncio
async def test_reply_handler_ignores_unpaired_user(store: Store) -> None:
    await store.create_pairing("token-1", "alice", None, 3600)
    await store.claim_pairing("token-1", "123", "123")
    await store.create_question_mapping("123", "100", "token-1", "q-1", 3600)

    update = _make_reply_update(user_id=999)
    context = MagicMock()

    await reply_handler(update, context, store, 3600)

    messages = await store.fetch_inbox(question_id="q-1")
    assert len(messages) == 0


@pytest.mark.asyncio
async def test_reply_handler_ignores_unknown_mapping(store: Store) -> None:
    await store.create_pairing("token-1", "alice", None, 3600)
    await store.claim_pairing("token-1", "123", "123")

    update = _make_reply_update(reply_to_message_id=999)
    context = MagicMock()

    await reply_handler(update, context, store, 3600)

    messages = await store.fetch_inbox(token="token-1")
    assert len(messages) == 0
