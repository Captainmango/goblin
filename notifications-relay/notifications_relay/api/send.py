from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from notifications_relay.api.deps import get_dispatcher, require_api_key
from notifications_relay.core.dispatcher import NotificationDispatcher
from notifications_relay.core.models import SendRequest, SendResponse
from notifications_relay.exceptions import (
    PairingNotFoundError,
    ProviderNotRegisteredError,
    SendError,
)

router = APIRouter(prefix="/send", dependencies=[Depends(require_api_key)])


@router.post("", response_model=SendResponse)
async def send_message(
    request: SendRequest,
    dispatcher: NotificationDispatcher = Depends(get_dispatcher),
) -> SendResponse:
    """Send a message to the operator bound to ``token``."""
    try:
        return await dispatcher.send(request)
    except PairingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderNotRegisteredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
