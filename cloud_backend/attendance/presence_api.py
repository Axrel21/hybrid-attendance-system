"""Presence event ingestion route — surveillance transport only (D3 Track 4)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cloud_backend.attendance.presence_handler import PresenceEventHandler
from cloud_backend.attendance.presence_timeline import get_timeline_service
from cloud_backend.attendance.schemas.presence import (
    PresenceEvent,
    PresenceEventResult,
    PresenceSessionListResponse,
    PresenceSessionResponse,
)

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


def _sessions_response(camera_id: str | None = None) -> PresenceSessionListResponse:
    sessions = [
        PresenceSessionResponse(**session.to_dict())
        for session in get_timeline_service().list_sessions(camera_id=camera_id)
    ]
    return PresenceSessionListResponse(total=len(sessions), sessions=sessions)


@router.get("/sessions", response_model=PresenceSessionListResponse)
async def list_presence_sessions() -> PresenceSessionListResponse:
    """List anonymous presence sessions aggregated from events."""
    return _sessions_response()


@router.get("/sessions/{camera_id}", response_model=PresenceSessionListResponse)
async def list_presence_sessions_for_camera(camera_id: str) -> PresenceSessionListResponse:
    """List presence sessions for one camera."""
    return _sessions_response(camera_id=camera_id)
