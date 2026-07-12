from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def inbox_client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-api-key"},
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_inbox_returns_and_deletes_replies(inbox_client: AsyncClient, app: FastAPI) -> None:
    await app.state.store.create_inbox(
        message_id="msg-1",
        token="token-1",
        telegram_user_id="12345",
        chat_id="12345",
        question_id="q-1",
        text="Yes",
        ttl_seconds=3600,
    )

    response = await inbox_client.get("/inbox", params={"question_id": "q-1"})
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["text"] == "Yes"

    second = await inbox_client.get("/inbox", params={"question_id": "q-1"})
    assert second.json()["messages"] == []


@pytest.mark.asyncio
async def test_inbox_requires_filter(inbox_client: AsyncClient) -> None:
    response = await inbox_client.get("/inbox")
    assert response.status_code == 400
