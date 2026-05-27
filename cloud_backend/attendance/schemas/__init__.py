"""Attendance-intelligence Pydantic schemas."""

from cloud_backend.attendance.schemas.attendance import AttendanceRecordResponse, AttendanceSummary
from cloud_backend.attendance.schemas.lecture import LectureCreate, LectureListResponse, LectureResponse
from cloud_backend.attendance.schemas.recognition import IngestionResult, RecognitionEvent
from cloud_backend.attendance.schemas.visibility import (
    ActiveLectureByClassroomEntry,
    ActiveLectureSummaryResponse,
    ActiveLecturesByClassroomResponse,
    AttendanceEventInspectionResponse,
    AttendanceEventListResponse,
    AttendanceRecordInspectionResponse,
    AttendanceRecordListResponse,
    CameraSourceListResponse,
    CameraSourceResponse,
    RecognitionLogEntryResponse,
    RecognitionLogListResponse,
)

__all__ = [
    "ActiveLectureByClassroomEntry",
    "ActiveLectureSummaryResponse",
    "ActiveLecturesByClassroomResponse",
    "CameraSourceListResponse",
    "CameraSourceResponse",
    "AttendanceEventInspectionResponse",
    "AttendanceEventListResponse",
    "AttendanceRecordInspectionResponse",
    "AttendanceRecordListResponse",
    "AttendanceRecordResponse",
    "AttendanceSummary",
    "IngestionResult",
    "LectureCreate",
    "LectureListResponse",
    "LectureResponse",
    "RecognitionEvent",
    "RecognitionLogEntryResponse",
    "RecognitionLogListResponse",
]
