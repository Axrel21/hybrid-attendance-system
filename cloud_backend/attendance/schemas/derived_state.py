"""Pydantic schemas for derived attendance states (D5 Track 2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DerivedAttendanceState = Literal[
    "candidate",
    "confirmed",
    "insufficient_presence",
    "manual_review",
    "expired",
]


class AttendanceStateRecord(BaseModel):
    """Derived attendance state — recomputed from decisions, not persisted to ORM."""

    student_id: str
    attendance_state: DerivedAttendanceState
    source: str = Field(default="decision_engine")
    lecture_id: str | None = None
    decision: str | None = Field(
        default=None,
        description="Advisory decision that produced this state",
    )
    reason: str | None = None


class AttendanceStateListResponse(BaseModel):
    """Response from GET /attendance/states."""

    total: int = Field(..., ge=0)
    records: list[AttendanceStateRecord]
