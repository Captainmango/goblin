from __future__ import annotations

from fastapi import APIRouter, Depends

from notifications_relay.api.deps import get_dispatcher
from notifications_relay.core.dispatcher import NotificationDispatcher
from notifications_relay.core.models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(
    dispatcher: NotificationDispatcher = Depends(get_dispatcher),
) -> HealthResponse:
    """Public health check."""
    return HealthResponse(
        status="ok",
        telegram_provider_registered=dispatcher.telegram_registered,
    )
