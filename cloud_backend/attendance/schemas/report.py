"""Pydantic schemas for attendance reporting (D5 Track 4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AttendanceLectureReport(BaseModel):
    """Lecture-level summary from finalized or live derived states."""

    lecture_id: str
    total_students: int = Field(..., ge=0)
    confirmed: int = Field(..., ge=0)
    manual_review: int = Field(..., ge=0)
    insufficient_presence: int = Field(..., ge=0)
    expired: int = Field(..., ge=0)
    candidate: int = Field(default=0, ge=0)
    attendance_rate: float = Field(..., ge=0.0, le=1.0)
    finalized: bool = False


class AttendanceReportListResponse(BaseModel):
    """Response from GET /attendance/report."""

    total: int = Field(..., ge=0)
    lectures: list[AttendanceLectureReport]


class AttendanceStudentReport(BaseModel):
    """Student-level rollup across lectures."""

    student_id: str
    lectures: int = Field(..., ge=0)
    confirmed: int = Field(..., ge=0)
    manual_review: int = Field(default=0, ge=0)
    insufficient_presence: int = Field(default=0, ge=0)
    expired: int = Field(default=0, ge=0)
    attendance_rate: float = Field(..., ge=0.0, le=1.0)


class AttendanceStudentReportResponse(BaseModel):
    """Response from GET /attendance/report/student/{student_id}."""

    student: AttendanceStudentReport
