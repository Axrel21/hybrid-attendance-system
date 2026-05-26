"""Pydantic schemas for attendance decisions (D5 Track 1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AttendanceDecisionState = Literal["present", "absent", "manual_review"]
DecisionConfidence = Literal["low", "medium", "high"]


class AttendanceDecisionRecord(BaseModel):
    """Advisory attendance decision — does not mutate attendance records."""

    student_id: str
    decision: AttendanceDecisionState
    reason: str
    lecture_id: str | None = None
    presence_ratio: float = Field(..., ge=0.0)
    confidence: DecisionConfidence = "low"


class AttendanceDecisionListResponse(BaseModel):
    """Response from GET /attendance/decisions."""

    total: int = Field(..., ge=0)
    records: list[AttendanceDecisionRecord]
