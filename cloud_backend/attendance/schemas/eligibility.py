"""Pydantic schemas for attendance eligibility (D4 Track 2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EligibilityDecision = Literal["eligible", "insufficient_presence", "unknown"]


class AttendanceEligibilityRecord(BaseModel):
    """Eligibility advisory — does not mutate attendance state."""

    student_id: str
    presence_duration_sec: int = Field(..., ge=0)
    lecture_duration_sec: int = Field(..., ge=0)
    presence_ratio: float = Field(..., ge=0.0)
    decision: EligibilityDecision
    lecture_id: str | None = None


class AttendanceEligibilityListResponse(BaseModel):
    """Response from GET /attendance/eligibility."""

    total: int = Field(..., ge=0)
    records: list[AttendanceEligibilityRecord]
