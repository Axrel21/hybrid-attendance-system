"""Presence event ingestion route — surveillance transport only (D3 Track 4)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cloud_backend.attendance.presence_handler import PresenceEventHandler
from cloud_backend.attendance.schemas.presence import PresenceEvent, PresenceEventResult

router = APIRouter(prefix="/presence", tags=["presence"])


@router.post("/events", response_model=PresenceEventResult, status_code=200)
async def ingest_presence_event(payload: PresenceEvent) -> PresenceEventResult:
    """Accept a surveillance presence event.

    Does not invoke AttendanceEngine or store identity fields.
    """
    try:
        return PresenceEventHandler().ingest(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
