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
        return SendResponse(message_id=1, channel="telegram")


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
async def test_create_pairing_requires_provider(client: AsyncClient) -> None:
    response = await client.post("/pairings", json={"label": "alice"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_create_pairing(registered_client: AsyncClient) -> None:
    response = await registered_client.post("/pairings", json={"label": "alice", "username": "alice@example.com"})
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert "expires_at" in data


@pytest.mark.asyncio
async def test_list_pairings(registered_client: AsyncClient, registered_app: FastAPI) -> None:
    create_response = await registered_client.post("/pairings", json={"label": "alice"})
    token = create_response.json()["token"]
    await registered_app.state.store.claim_pairing(token, "12345", "12345")

    response = await registered_client.get("/pairings")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["label"] == "alice"


@pytest.mark.asyncio
async def test_revoke_pairing(registered_client: AsyncClient, registered_app: FastAPI) -> None:
    create_response = await registered_client.post("/pairings", json={"label": "alice"})
    token = create_response.json()["token"]
    await registered_app.state.store.claim_pairing(token, "12345", "12345")

    response = await registered_client.delete(f"/pairings/{token}")
    assert response.status_code == 204

    response = await registered_client.delete(f"/pairings/{token}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cleanup_expired(registered_client: AsyncClient, registered_app: FastAPI) -> None:
    await registered_app.state.store.create_pairing("expired-token", "expired", None, -1)
    response = await registered_client.delete("/pairings/expired")
    assert response.status_code == 200
    assert response.json()["deleted"] == 1


@pytest.mark.asyncio
async def test_pairings_require_auth(registered_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=registered_app),
        base_url="http://test",
    ) as client:
        response = await client.post("/pairings", json={"label": "alice"})
        assert response.status_code == 401
