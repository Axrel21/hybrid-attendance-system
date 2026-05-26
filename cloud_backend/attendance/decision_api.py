"""Attendance decision read API (D5 Track 1)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.decision_service import get_decision_service
from cloud_backend.attendance.schemas.decision import AttendanceDecisionListResponse
from cloud_backend.db.session import get_async_session

router = APIRouter(tags=["attendance-decision"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


async def _build_response(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
) -> AttendanceDecisionListResponse:
    service = get_decision_service()
    try:
        records = await service.build_records(session, lecture_id=lecture_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AttendanceDecisionListResponse(total=len(records), records=records)


@router.get("/attendance/decisions", response_model=AttendanceDecisionListResponse)
async def list_attendance_decisions(
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceDecisionListResponse:
    """Advisory attendance decisions from eligibility and evidence confidence."""
    return await _build_response(session)


@router.get(
    "/attendance/decisions/{lecture_id}",
    response_model=AttendanceDecisionListResponse,
)
async def list_attendance_decisions_for_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceDecisionListResponse:
    """Attendance decisions for one lecture."""
    return await _build_response(session, lecture_id=lecture_id)
