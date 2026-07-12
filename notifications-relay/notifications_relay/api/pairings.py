from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from notifications_relay.api.deps import (
    get_dispatcher,
    get_pairing_service,
    require_api_key,
)
from notifications_relay.core.dispatcher import NotificationDispatcher
from notifications_relay.core.models import (
    CleanupResponse,
    Pairing,
    PairingRequest,
    PairingResponse,
)
from notifications_relay.core.pairing import PairingService
from notifications_relay.exceptions import PairingNotFoundError

router = APIRouter(prefix="/pairings", dependencies=[Depends(require_api_key)])


@router.post("", response_model=PairingResponse)
async def create_pairing(
    request: PairingRequest,
    dispatcher: NotificationDispatcher = Depends(get_dispatcher),
    pairing_service: PairingService = Depends(get_pairing_service),
) -> PairingResponse:
    """Create a pending pairing token."""
    if not dispatcher.telegram_registered:
        raise HTTPException(
            status_code=503,
            detail="Telegram provider is not registered",
        )
    return await pairing_service.create(label=request.label, username=request.username)


@router.get("", response_model=list[Pairing])
async def list_pairings(
    pairing_service: PairingService = Depends(get_pairing_service),
) -> list[Pairing]:
    """List active pairings."""
    rows = await pairing_service.store.list_active_pairings()
    return [Pairing(**row) for row in rows]


@router.delete("/expired", response_model=CleanupResponse)
async def cleanup_expired(
    pairing_service: PairingService = Depends(get_pairing_service),
) -> CleanupResponse:
    """Delete expired pending pairings."""
    deleted = await pairing_service.cleanup_expired()
    return CleanupResponse(deleted=deleted)


@router.delete("/{token}", status_code=204)
async def revoke_pairing(
    token: str,
    pairing_service: PairingService = Depends(get_pairing_service),
) -> None:
    """Revoke an active pairing."""
    try:
        revoked = await pairing_service.revoke(token)
    except PairingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not revoked:
        raise HTTPException(status_code=404, detail="Pairing not found")
