"""Pydantic schemas for lecture session flows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from cloud_backend.attendance.schemas.attendance import AttendanceSummary


class LectureCreate(BaseModel):
    subject_id: uuid.UUID
    classroom_id: uuid.UUID
    scheduled_start: datetime
    scheduled_end: datetime
    attendance_window_minutes: int = Field(default=15, ge=1, le=240)


class LectureResponse(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    subject_code: str
    subject_name: str
    classroom_id: uuid.UUID
    classroom_name: str
    status: str
    scheduled_start: datetime
    scheduled_end: datetime
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    attendance_window_minutes: int
    created_at: datetime
    attendance_summary: AttendanceSummary


class LectureListResponse(BaseModel):
    total: int
    lectures: list[LectureResponse]
