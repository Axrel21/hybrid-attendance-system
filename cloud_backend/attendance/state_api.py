"""Derived attendance state read API (D5 Track 2)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.schemas.derived_state import AttendanceStateListResponse
from cloud_backend.attendance.state_service import get_state_service
from cloud_backend.db.session import get_async_session

router = APIRouter(tags=["attendance-state"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


async def _build_response(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
) -> AttendanceStateListResponse:
    service = get_state_service()
    try:
        records = await service.build_records(session, lecture_id=lecture_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AttendanceStateListResponse(total=len(records), records=records)


@router.get("/attendance/states", response_model=AttendanceStateListResponse)
async def list_attendance_states(
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceStateListResponse:
    """Recompute derived attendance states from advisory decisions."""
    return await _build_response(session)


@router.get(
    "/attendance/states/{lecture_id}",
    response_model=AttendanceStateListResponse,
)
async def list_attendance_states_for_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceStateListResponse:
    """Derived attendance states for one lecture."""
    return await _build_response(session, lecture_id=lecture_id)
