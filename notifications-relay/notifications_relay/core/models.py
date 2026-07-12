from __future__ import annotations

from pydantic import BaseModel


class PairingRequest(BaseModel):
    """Request body for creating a pending pairing."""

    label: str | None = None
    username: str | None = None


class PairingResponse(BaseModel):
    """Response returned when a pending pairing is created."""

    token: str
    expires_at: str


class Pairing(BaseModel):
    """Active or pending pairing record."""

    token: str
    label: str | None
    username: str | None
    telegram_user_id: str | None
    chat_id: str | None
    status: str
    created_at: str
    expires_at: str
    claimed_at: str | None


class SendRequest(BaseModel):
    """Request body for sending a message."""

    token: str
    text: str
    question_id: str | None = None
    idempotency_key: str | None = None


class SendResponse(BaseModel):
    """Response returned after a message is sent."""

    message_id: int
    channel: str


class InboxMessage(BaseModel):
    """A single inbound reply stored in the inbox."""

    id: str
    token: str
    telegram_user_id: str
    chat_id: str
    question_id: str | None
    text: str
    received_at: str


class InboxResponse(BaseModel):
    """Response returned by the inbox endpoint."""

    messages: list[InboxMessage]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    telegram_provider_registered: bool


class CleanupResponse(BaseModel):
    """Response returned by the expired-pairings cleanup endpoint."""

    deleted: int
