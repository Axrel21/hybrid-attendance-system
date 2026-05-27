"""Pydantic schemas for finalized attendance states (D5 Track 3)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from cloud_backend.attendance.schemas.derived_state import DerivedAttendanceState


class AttendanceFinalizedRecord(BaseModel):
    """Finalized or live derived attendance state for a lecture."""

    student_id: str
    attendance_state: DerivedAttendanceState
    finalized: bool = Field(
        ...,
        description="True when lecture is ended and states are frozen",
    )
    lecture_id: str | None = None
    source: str = Field(default="decision_engine")


class AttendanceFinalizedListResponse(BaseModel):
    """Response from GET /attendance/finalized."""

    total: int = Field(..., ge=0)
    records: list[AttendanceFinalizedRecord]
