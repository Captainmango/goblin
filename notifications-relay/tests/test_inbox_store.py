from __future__ import annotations

import pytest
from notifications_relay.store import Store


@pytest.mark.asyncio
async def test_insert_and_fetch_inbox(store: Store) -> None:
    await store.create_inbox("msg-1", "token-1", "user-1", "chat-1", "q-1", "Yes", 3600)
    messages = await store.fetch_inbox(question_id="q-1")
    assert len(messages) == 1
    assert messages[0]["text"] == "Yes"
    assert messages[0]["token"] == "token-1"


@pytest.mark.asyncio
async def test_fetch_consumes_messages(store: Store) -> None:
    await store.create_inbox("msg-1", "token-1", "user-1", "chat-1", "q-1", "Yes", 3600)
    first = await store.fetch_inbox(question_id="q-1")
    assert len(first) == 1
    second = await store.fetch_inbox(question_id="q-1")
    assert len(second) == 0


@pytest.mark.asyncio
async def test_fetch_by_token(store: Store) -> None:
    await store.create_inbox("msg-1", "token-1", "user-1", "chat-1", None, "Hello", 3600)
    messages = await store.fetch_inbox(token="token-1")
    assert len(messages) == 1
    assert messages[0]["question_id"] is None


@pytest.mark.asyncio
async def test_delete_expired_inbox(store: Store) -> None:
    await store.create_inbox("msg-1", "token-1", "user-1", "chat-1", "q-1", "Yes", -1)
    deleted = await store.delete_expired_inbox()
    assert deleted == 1
    messages = await store.fetch_inbox(question_id="q-1")
    assert len(messages) == 0
