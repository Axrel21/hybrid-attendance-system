"""Pydantic schemas for Phase 4 operational visibility APIs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from cloud_backend.attendance.schemas.attendance import AttendanceSummary
from cloud_backend.attendance.schemas.lecture import LectureResponse


class AttendanceProgressionMeta(BaseModel):
    """Per-record progression context derived from AttendanceEvent lineage."""

    attendance_event_count: int = 0
    last_transition_at: Optional[datetime] = None
    last_accumulation_at: Optional[datetime] = None


class AttendanceRecordInspectionResponse(BaseModel):
    id: uuid.UUID
    lecture_id: uuid.UUID
    student_id: uuid.UUID
    student_no: str
    student_name: str
    gallery_identity: Optional[str] = None
    state: str
    exception_type: Optional[str] = None
    exception_reason: Optional[str] = None
    is_locked: bool
    last_event_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    progression: AttendanceProgressionMeta


class AttendanceRecordListResponse(BaseModel):
    lecture_id: uuid.UUID
    total: int
    records: list[AttendanceRecordInspectionResponse]


class AttendanceEventInspectionResponse(BaseModel):
    id: uuid.UUID
    attendance_record_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    event_type: str
    from_state: str
    to_state: str
    semantic: Literal["accumulation", "transition"]
    source: str
    confidence: Optional[float] = None
    timestamp_ms: Optional[int] = None
    created_at: datetime


class AttendanceEventListResponse(BaseModel):
    lecture_id: uuid.UUID
    total: int
    events: list[AttendanceEventInspectionResponse]


class RecognitionLogEntryResponse(BaseModel):
    id: uuid.UUID
    lecture_id: Optional[uuid.UUID] = None
    classroom_id: Optional[uuid.UUID] = None
    camera_id: Optional[str] = None
    gallery_identity: str
    confidence: float
    source: str
    timestamp_ms: Optional[int] = None
    received_at: datetime
    accepted: bool
    outcome: str = Field(
        description="Human-readable processing outcome: accepted, rejected, or suppressed",
    )


class RecognitionLogListResponse(BaseModel):
    total: int
    logs: list[RecognitionLogEntryResponse]


class ActiveLectureSummaryResponse(BaseModel):
    """Summary of the currently active lecture window, if any.

    When ``classroom_id`` is supplied to the endpoint, scopes to that classroom.
    Without it, returns the most recently started active lecture (D.1 compat).
    """

    active: bool
    lecture: Optional[LectureResponse] = None
    attendance_summary: Optional[AttendanceSummary] = None
    classroom_id: Optional[uuid.UUID] = None
    resolution_mode: str = Field(
        default="global",
        description="'classroom_scoped' or 'global'",
    )


class ActiveLectureByClassroomEntry(BaseModel):
    classroom_id: uuid.UUID
    classroom_name: str
    lecture: LectureResponse
    attendance_summary: AttendanceSummary


class ActiveLecturesByClassroomResponse(BaseModel):
    """All simultaneously active lectures, one per classroom."""

    total: int
    active_lectures: list[ActiveLectureByClassroomEntry]


class CameraSourceResponse(BaseModel):
    id: uuid.UUID
    camera_id: str
    classroom_id: uuid.UUID
    classroom_name: str
    label: Optional[str] = None
    location: Optional[str] = None
    active: bool


class CameraSourceListResponse(BaseModel):
    total: int
    sources: list[CameraSourceResponse]
