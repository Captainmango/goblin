from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from notifications_relay.core.models import InboxMessage, Pairing, PairingResponse, SendResponse
from notifications_relay.exceptions import AskTimeoutError

DEFAULT_POLL_INTERVAL = 1.0


class RelayClient:
    """Async HTTP client for the notifications relay."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> RelayClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        await self.close()

    async def create_pairing(
        self,
        label: str | None = None,
        username: str | None = None,
    ) -> PairingResponse:
        """Request a new pairing token from the relay."""
        payload: dict[str, Any] = {}
        if label is not None:
            payload["label"] = label
        if username is not None:
            payload["username"] = username
        response = await self._client.post("/pairings", json=payload)
        response.raise_for_status()
        return PairingResponse(**response.json())

    async def list_pairings(self) -> list[Pairing]:
        """List active pairings."""
        response = await self._client.get("/pairings")
        response.raise_for_status()
        return [Pairing(**item) for item in response.json()]

    async def revoke(self, token: str) -> None:
        """Revoke a pairing token."""
        response = await self._client.delete(f"/pairings/{token}")
        if response.status_code == 404:
            raise KeyError(f"Pairing not found: {token}")
        response.raise_for_status()

    async def send(
        self,
        token: str,
        text: str,
        question_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SendResponse:
        """Send a message to the operator bound to ``token``."""
        payload: dict[str, Any] = {
            "token": token,
            "text": text,
        }
        if question_id is not None:
            payload["question_id"] = question_id
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        response = await self._client.post("/send", json=payload)
        response.raise_for_status()
        return SendResponse(**response.json())

    async def get_inbox(
        self,
        question_id: str | None = None,
        token: str | None = None,
    ) -> list[InboxMessage]:
        """Fetch and consume replies from the inbox."""
        params: dict[str, str] = {}
        if question_id is not None:
            params["question_id"] = question_id
        if token is not None:
            params["token"] = token
        response = await self._client.get("/inbox", params=params)
        response.raise_for_status()
        return [InboxMessage(**item) for item in response.json()["messages"]]

    async def ask(
        self,
        token: str,
        text: str,
        question_id: str,
        timeout: float = 300.0,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> InboxMessage:
        """Send a question and poll the inbox until a reply arrives."""
        await self.send(token, text, question_id=question_id)
        deadline = time.monotonic() + timeout
        while True:
            messages = await self.get_inbox(question_id=question_id)
            if messages:
                return messages[0]
            if time.monotonic() >= deadline:
                raise AskTimeoutError(f"No reply received for question {question_id} within {timeout}s")
            await asyncio.sleep(poll_interval)
