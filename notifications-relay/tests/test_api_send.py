from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from notifications_relay.core.models import SendResponse


class FakeProvider:
    name = "telegram"

    async def send(
        self,
        token: str,
        text: str,
        question_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResponse:
        return SendResponse(message_id=42, channel="telegram")


@pytest.fixture
def registered_app(app: FastAPI) -> FastAPI:
    app.state.dispatcher.register("telegram", FakeProvider())
    return app


@pytest_asyncio.fixture
async def registered_client(registered_app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=registered_app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-api-key"},
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_send_resolves_token_and_stores_mapping(
    registered_client: AsyncClient,
    registered_app: FastAPI,
) -> None:
    token_response = await registered_client.post("/pairings", json={"label": "alice"})
    token = token_response.json()["token"]
    await registered_app.state.store.claim_pairing(token, "12345", "12345")

    response = await registered_client.post(
        "/send",
        json={"token": token, "text": "Hello", "question_id": "q-1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message_id"] == 42
    assert data["channel"] == "telegram"

    mapping = await registered_app.state.store.get_question_mapping("12345", "42")
    assert mapping is not None
    assert mapping["question_id"] == "q-1"


@pytest.mark.asyncio
async def test_send_pairing_not_found(registered_client: AsyncClient) -> None:
    response = await registered_client.post(
        "/send",
        json={"token": "unknown-token", "text": "Hello"},
    )
    assert response.status_code == 404
