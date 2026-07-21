from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from notifications_relay.api.deps import get_inbox_service, require_api_key
from notifications_relay.core.inbox import InboxService
from notifications_relay.core.models import InboxResponse

router = APIRouter(prefix="/inbox", dependencies=[Depends(require_api_key)])


@router.get("", response_model=InboxResponse)
async def get_inbox(
    question_id: str | None = Query(None),
    token: str | None = Query(None),
    inbox_service: InboxService = Depends(get_inbox_service),
) -> InboxResponse:
    """Fetch and consume replies.

    Pairing tokens are accepted as a query parameter for local development
    convenience (the relay is intended to run on ``127.0.0.1``). For any
    production deployment, terminate TLS and consider moving authentication
    to an HTTP header instead.
    """
    if not question_id and not token:
        raise HTTPException(
            status_code=400,
            detail="At least one of question_id or token is required",
        )
    messages = await inbox_service.fetch(question_id=question_id, token=token)
    return InboxResponse(messages=messages)
