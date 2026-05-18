"""Attendance-intelligence Pydantic schemas."""

from cloud_backend.attendance.schemas.attendance import AttendanceRecordResponse, AttendanceSummary
from cloud_backend.attendance.schemas.lecture import LectureCreate, LectureListResponse, LectureResponse
from cloud_backend.attendance.schemas.recognition import IngestionResult, RecognitionEvent

__all__ = [
    "AttendanceRecordResponse",
    "AttendanceSummary",
    "IngestionResult",
    "LectureCreate",
    "LectureListResponse",
    "LectureResponse",
    "RecognitionEvent",
]
