"""Attendance reporting API for dashboard consumption (D5 Track 4)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.report_service import get_report_service
from cloud_backend.attendance.schemas.report import (
    AttendanceLectureReport,
    AttendanceReportListResponse,
    AttendanceStudentReport,
    AttendanceStudentReportResponse,
)
from cloud_backend.db.session import get_async_session

router = APIRouter(tags=["attendance-report"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


@router.get("/attendance/report", response_model=AttendanceReportListResponse)
async def list_attendance_report(
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceReportListResponse:
    """Lecture summaries across all derived/finalized states."""
    service = get_report_service()
    try:
        lectures = await service.build_lecture_reports(session)
    except SQLAlchemyError:
        return AttendanceReportListResponse(total=0, lectures=[])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AttendanceReportListResponse(total=len(lectures), lectures=lectures)


@router.get(
    "/attendance/report/student/{student_id}",
    response_model=AttendanceStudentReportResponse,
)
async def get_attendance_student_report(
    student_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceStudentReportResponse:
    """Student rollup across lectures."""
    service = get_report_service()
    try:
        student = await service.build_student_report(session, student_id=student_id)
    except SQLAlchemyError:
        student = AttendanceStudentReport(
            student_id=student_id,
            lectures=0,
            confirmed=0,
            manual_review=0,
            insufficient_presence=0,
            expired=0,
            attendance_rate=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AttendanceStudentReportResponse(student=student)


@router.get(
    "/attendance/report/{lecture_id}",
    response_model=AttendanceLectureReport,
)
async def get_attendance_lecture_report(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceLectureReport:
    """Single lecture summary."""
    service = get_report_service()
    try:
        lectures = await service.build_lecture_reports(session, lecture_id=lecture_id)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=404,
            detail=f"No report data for lecture {lecture_id}",
        ) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not lectures:
        raise HTTPException(status_code=404, detail=f"No report data for lecture {lecture_id}")

    return lectures[0]
