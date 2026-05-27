"""Pydantic schemas for attendance records and summaries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AttendanceSummary(BaseModel):
    lecture_id: uuid.UUID
    total_enrolled: int = 0
    undetected: int = 0
    confirmed: int = 0
    candidate: int = 0
    initialized: int = 0
    absent: int = 0
    exception_count: int = 0


class AttendanceRecordResponse(BaseModel):
    id: uuid.UUID
    lecture_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    state: str
    exception_type: Optional[str] = None
    exception_reason: Optional[str] = None
    is_locked: bool
    last_event_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
