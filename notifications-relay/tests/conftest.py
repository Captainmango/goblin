from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from notifications_relay.config import RelayConfig
from notifications_relay.core.dispatcher import NotificationDispatcher
from notifications_relay.core.inbox import InboxService
from notifications_relay.core.pairing import PairingService
from notifications_relay.main import create_app
from notifications_relay.store import Store


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "relay.db"


@pytest_asyncio.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[Store]:
    store = Store(temp_db_path)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def relay_config(temp_db_path: Path) -> RelayConfig:
    return RelayConfig(
        api_key="test-api-key",
        host="127.0.0.1",
        port=8000,
        db_path=temp_db_path,
    )


@pytest_asyncio.fixture
async def app(relay_config: RelayConfig) -> AsyncGenerator[FastAPI]:
    app_instance = create_app(relay_config)
    async with app_instance.router.lifespan_context(app_instance):
        yield app_instance


@pytest_asyncio.fixture
async def client(app: FastAPI, relay_config: RelayConfig) -> AsyncGenerator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {relay_config.api_key.get_secret_value()}"},
    ) as client:
        yield client


@pytest.fixture
def pairing_service(store: Store, relay_config: RelayConfig) -> PairingService:
    return PairingService(store, token_ttl_seconds=relay_config.pairing_token_ttl_seconds)


@pytest.fixture
def inbox_service(store: Store, relay_config: RelayConfig) -> InboxService:
    return InboxService(store, ttl_seconds=relay_config.question_mapping_ttl_seconds)


@pytest.fixture
def dispatcher(store: Store) -> NotificationDispatcher:
    return NotificationDispatcher(store)
