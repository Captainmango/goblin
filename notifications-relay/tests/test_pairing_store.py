from __future__ import annotations

import pytest

from notifications_relay.store import Store


@pytest.mark.asyncio
async def test_create_and_get_pairing(store: Store) -> None:
    await store.create_pairing("token-1", "alice", "alice@example.com", 3600)
    pairing = await store.get_pairing_by_token("token-1")
    assert pairing is not None
    assert pairing["label"] == "alice"
    assert pairing["username"] == "alice@example.com"
    assert pairing["status"] == "pending"


@pytest.mark.asyncio
async def test_claim_pairing(store: Store) -> None:
    await store.create_pairing("token-1", "alice", None, 3600)
    await store.claim_pairing("token-1", "12345", "12345")
    pairing = await store.get_pairing_by_token("token-1")
    assert pairing is not None
    assert pairing["status"] == "claimed"
    assert pairing["telegram_user_id"] == "12345"
    assert pairing["chat_id"] == "12345"
    assert pairing["claimed_at"] is not None


@pytest.mark.asyncio
async def test_list_active_pairings(store: Store) -> None:
    await store.create_pairing("token-1", "alice", None, 3600)
    await store.create_pairing("token-2", "bob", None, 3600)
    await store.claim_pairing("token-1", "1", "1")
    await store.claim_pairing("token-2", "2", "2")
    active = await store.list_active_pairings()
    assert len(active) == 2


@pytest.mark.asyncio
async def test_revoke_pairing(store: Store) -> None:
    await store.create_pairing("token-1", "alice", None, 3600)
    await store.claim_pairing("token-1", "1", "1")
    revoked = await store.revoke_pairing("token-1")
    assert revoked is True
    pairing = await store.get_pairing_by_token("token-1")
    assert pairing is not None
    assert pairing["status"] == "revoked"


@pytest.mark.asyncio
async def test_delete_expired_pending(store: Store) -> None:
    await store.create_pairing("token-1", "alice", None, -1)
    deleted = await store.delete_expired_pending()
    assert deleted == 1
    pairing = await store.get_pairing_by_token("token-1")
    assert pairing is None
