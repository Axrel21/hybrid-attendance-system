"""Pydantic schemas for attendance evidence (D4 Track 1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EvidenceState = Literal["unknown", "recognized_only", "presence_observed"]
EvidenceConfidence = Literal["low", "medium", "high"]


class AttendanceEvidenceRecord(BaseModel):
    """Correlation output — not a final attendance decision."""

    student_id: str = Field(..., description="Student UUID or gallery_identity fallback")
    evidence: EvidenceState
    confidence: EvidenceConfidence
    recognized_at: int = Field(..., description="Recognition wall-clock ms")
    camera_id: str | None = None
    lecture_id: str | None = None
    classroom_id: str | None = None
    presence_camera_id: str | None = None
    presence_track_id: int | None = None


class AttendanceEvidenceListResponse(BaseModel):
    """Response from GET /attendance/evidence."""

    total: int = Field(..., ge=0)
    records: list[AttendanceEvidenceRecord]
