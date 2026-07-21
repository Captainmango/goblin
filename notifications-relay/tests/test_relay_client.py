from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from notifications_relay.client import RelayClient
from notifications_relay.config import RelayConfig
from notifications_relay.core.models import SendResponse
from notifications_relay.exceptions import AskTimeoutError
from notifications_relay.main import create_app


class FakeProvider:
    name = "telegram"

    async def send(
        self,
        token: str,
        text: str,
        question_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResponse:
        return SendResponse(message_id=1, channel="telegram")


@pytest_asyncio.fixture
async def client_app(tmp_path) -> FastAPI:
    config = RelayConfig(
        api_key="test-api-key",
        db_path=tmp_path / "relay.db",
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        app.state.dispatcher.register("telegram", FakeProvider())
        yield app


@pytest.mark.asyncio
async def test_client_create_pairing(client_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=client_app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-api-key"},
    ) as transport_client:
        client = RelayClient(
            base_url="http://test",
            api_key="test-api-key",
        )
        client._client = transport_client
        response = await client.create_pairing(label="alice")
        assert response.token
        assert response.expires_at


@pytest.mark.asyncio
async def test_client_ask_timeout(client_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=client_app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-api-key"},
    ) as transport_client:
        client = RelayClient(
            base_url="http://test",
            api_key="test-api-key",
        )
        client._client = transport_client
        pairing = await client.create_pairing(label="alice")
        await client_app.state.store.claim_pairing(pairing.token, "123", "123")

        with pytest.raises(AskTimeoutError):
            await client.ask(
                pairing.token,
                "Are you there?",
                question_id="q-timeout",
                timeout=0.05,
                poll_interval=0.01,
            )
