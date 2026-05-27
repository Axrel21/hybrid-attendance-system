"""Attendance evidence read API (D4 Track 1)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.api.visibility_queries import VISIBILITY_TELEMETRY_LIMIT
from cloud_backend.attendance.evidence_service import get_evidence_service
from cloud_backend.attendance.schemas.evidence import AttendanceEvidenceListResponse
from cloud_backend.classroom.resolver import fetch_all_active_lectures
from cloud_backend.db.session import get_async_session

router = APIRouter(tags=["attendance-evidence"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


async def _active_lecture_ids(session: AsyncSession) -> set[str]:
    lectures = await fetch_all_active_lectures(session)
    return {str(lecture.id) for lecture in lectures}


async def _build_response(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
) -> AttendanceEvidenceListResponse:
    active_ids = await _active_lecture_ids(session)
    if not active_ids:
        return AttendanceEvidenceListResponse(total=0, records=[])

    if lecture_id is not None and str(lecture_id) not in active_ids:
        return AttendanceEvidenceListResponse(total=0, records=[])

    service = get_evidence_service()
    try:
        records = await service.build_records(
            session,
            lecture_id=lecture_id,
            limit=VISIBILITY_TELEMETRY_LIMIT,
        )
    except SQLAlchemyError:
        return AttendanceEvidenceListResponse(total=0, records=[])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    records = [r for r in records if r.lecture_id in active_ids]
    if lecture_id is not None:
        lecture_key = str(lecture_id)
        records = [r for r in records if r.lecture_id == lecture_key]

    return AttendanceEvidenceListResponse(total=len(records), records=records)


@router.get("/attendance/evidence", response_model=AttendanceEvidenceListResponse)
async def list_attendance_evidence(
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceEvidenceListResponse:
    """Correlate recognition logs with anonymous presence sessions."""
    return await _build_response(session)


@router.get(
    "/attendance/evidence/{lecture_id}",
    response_model=AttendanceEvidenceListResponse,
)
async def list_attendance_evidence_for_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceEvidenceListResponse:
    """Evidence records for one lecture."""
    return await _build_response(session, lecture_id=lecture_id)
