"""Attendance finalization read API (D5 Track 3)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.finalization_service import get_finalization_service
from cloud_backend.attendance.schemas.finalized import AttendanceFinalizedListResponse
from cloud_backend.db.session import get_async_session

router = APIRouter(tags=["attendance-finalization"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


async def _build_response(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
) -> AttendanceFinalizedListResponse:
    service = get_finalization_service()
    try:
        records = await service.build_records(session, lecture_id=lecture_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AttendanceFinalizedListResponse(total=len(records), records=records)


@router.get("/attendance/finalized", response_model=AttendanceFinalizedListResponse)
async def list_attendance_finalized(
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceFinalizedListResponse:
    """Live states for active lectures; frozen states for ended lectures."""
    return await _build_response(session)


@router.get(
    "/attendance/finalized/{lecture_id}",
    response_model=AttendanceFinalizedListResponse,
)
async def list_attendance_finalized_for_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceFinalizedListResponse:
    """Finalized or live states for one lecture."""
    return await _build_response(session, lecture_id=lecture_id)
