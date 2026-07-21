from __future__ import annotations

import pytest

from notifications_relay.store import Store


@pytest.mark.asyncio
async def test_create_and_resolve_mapping(store: Store) -> None:
    await store.create_question_mapping("chat-1", "msg-1", "token-1", "q-1", 3600)
    mapping = await store.get_question_mapping("chat-1", "msg-1")
    assert mapping is not None
    assert mapping["question_id"] == "q-1"
    assert mapping["token"] == "token-1"


@pytest.mark.asyncio
async def test_mapping_missing_key(store: Store) -> None:
    mapping = await store.get_question_mapping("chat-1", "msg-99")
    assert mapping is None


@pytest.mark.asyncio
async def test_delete_expired_mappings(store: Store) -> None:
    await store.create_question_mapping("chat-1", "msg-1", "token-1", "q-1", -1)
    deleted = await store.delete_expired_question_mappings()
    assert deleted == 1
    mapping = await store.get_question_mapping("chat-1", "msg-1")
    assert mapping is None
