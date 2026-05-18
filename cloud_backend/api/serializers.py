"""Map ORM models to attendance-intelligence API schemas."""

from __future__ import annotations

import uuid

from cloud_backend.attendance.state_machine import AttendanceState
from cloud_backend.models.attendance_record import AttendanceRecord
from cloud_backend.models.lecture import Lecture
from cloud_backend.attendance.schemas.attendance import AttendanceRecordResponse, AttendanceSummary
from cloud_backend.attendance.schemas.lecture import LectureResponse


_EXCEPTION_STATES = {
    AttendanceState.LATE_ENTRY.value,
    AttendanceState.TECH_DROPOUT.value,
    AttendanceState.MANUAL_OVERRIDE.value,
}


def build_attendance_summary(lecture: Lecture) -> AttendanceSummary:
    counts = {state.value: 0 for state in AttendanceState}
    for record in lecture.attendance_records:
        counts[record.state] = counts.get(record.state, 0) + 1

    exception_count = sum(counts[state] for state in _EXCEPTION_STATES)

    return AttendanceSummary(
        lecture_id=lecture.id,
        total_enrolled=len(lecture.attendance_records),
        undetected=counts[AttendanceState.UNDETECTED.value],
        confirmed=counts[AttendanceState.CONFIRMED.value],
        candidate=counts[AttendanceState.CANDIDATE.value],
        initialized=counts[AttendanceState.INITIALIZED.value],
        absent=counts[AttendanceState.ABSENT.value],
        exception_count=exception_count,
    )


def build_lecture_response(lecture: Lecture) -> LectureResponse:
    return LectureResponse(
        id=lecture.id,
        subject_id=lecture.subject_id,
        subject_code=lecture.subject.code,
        subject_name=lecture.subject.name,
        classroom_id=lecture.classroom_id,
        classroom_name=lecture.classroom.name,
        status=lecture.status,
        scheduled_start=lecture.scheduled_start,
        scheduled_end=lecture.scheduled_end,
        actual_start=lecture.actual_start,
        actual_end=lecture.actual_end,
        attendance_window_minutes=lecture.attendance_window_minutes,
        created_at=lecture.created_at,
        attendance_summary=build_attendance_summary(lecture),
    )


def build_attendance_record_response(
    record: AttendanceRecord,
    *,
    student_name: str,
) -> AttendanceRecordResponse:
    return AttendanceRecordResponse(
        id=record.id,
        lecture_id=record.lecture_id,
        student_id=record.student_id,
        student_name=student_name,
        state=record.state,
        exception_type=record.exception_type,
        exception_reason=record.exception_reason,
        is_locked=record.is_locked,
        last_event_at=record.last_event_at,
        confirmed_at=record.confirmed_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
