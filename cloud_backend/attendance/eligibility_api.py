"""Attendance eligibility read API (D4 Track 2)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.eligibility_service import get_eligibility_service
from cloud_backend.attendance.schemas.eligibility import AttendanceEligibilityListResponse
from cloud_backend.db.session import get_async_session

router = APIRouter(tags=["attendance-eligibility"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


async def _build_response(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
) -> AttendanceEligibilityListResponse:
    service = get_eligibility_service()
    try:
        records = await service.build_records(session, lecture_id=lecture_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AttendanceEligibilityListResponse(total=len(records), records=records)


@router.get("/attendance/eligibility", response_model=AttendanceEligibilityListResponse)
async def list_attendance_eligibility(
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceEligibilityListResponse:
    """Compute eligibility from evidence and presence durations."""
    return await _build_response(session)


@router.get(
    "/attendance/eligibility/{lecture_id}",
    response_model=AttendanceEligibilityListResponse,
)
async def list_attendance_eligibility_for_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceEligibilityListResponse:
    """Eligibility for one lecture."""
    return await _build_response(session, lecture_id=lecture_id)
