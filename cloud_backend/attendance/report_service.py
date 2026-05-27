"""AttendanceReportService — reporting from finalized/live states (D5 Track 4)."""

from __future__ import annotations

import logging
import uuid
from collections import Counter, defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.finalization_service import get_finalization_service
from cloud_backend.attendance.schemas.finalized import AttendanceFinalizedRecord
from cloud_backend.attendance.schemas.report import (
    AttendanceLectureReport,
    AttendanceStudentReport,
)

log = logging.getLogger("cloud_backend.attendance.report")


class AttendanceReportService:
    """Build lecture and student summaries for dashboard consumption."""

    async def build_lecture_reports(
        self,
        session: AsyncSession,
        *,
        lecture_id: uuid.UUID | None = None,
        limit: int = 500,
    ) -> list[AttendanceLectureReport]:
        records = await get_finalization_service().build_records(
            session,
            lecture_id=lecture_id,
            limit=limit,
        )
        by_lecture = _group_by_lecture(records)

        reports: list[AttendanceLectureReport] = []
        for lec_key, lecture_records in sorted(by_lecture.items(), key=lambda item: item[0] or ""):
            if lec_key is None:
                continue
            reports.append(_summarize_lecture(lec_key, lecture_records))

        log.info("attendance lecture reports built total=%d filter=%s", len(reports), lecture_id)
        return reports

    async def build_student_report(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        limit: int = 500,
    ) -> AttendanceStudentReport:
        records = await get_finalization_service().build_records(session, limit=limit)
        student_records = [record for record in records if record.student_id == student_id]
        return _summarize_student(student_id, student_records)


def _group_by_lecture(
    records: list[AttendanceFinalizedRecord],
) -> dict[str | None, list[AttendanceFinalizedRecord]]:
    grouped: dict[str | None, list[AttendanceFinalizedRecord]] = defaultdict(list)
    for record in records:
        grouped[record.lecture_id].append(record)
    return grouped


def _summarize_lecture(
    lecture_id: str,
    records: list[AttendanceFinalizedRecord],
) -> AttendanceLectureReport:
    counts = Counter(record.attendance_state for record in records)
    total = len(records)
    confirmed = counts.get("confirmed", 0)
    rate = (confirmed / total) if total else 0.0
    finalized = any(record.finalized for record in records)

    return AttendanceLectureReport(
        lecture_id=lecture_id,
        total_students=total,
        confirmed=confirmed,
        manual_review=counts.get("manual_review", 0),
        insufficient_presence=counts.get("insufficient_presence", 0),
        expired=counts.get("expired", 0),
        candidate=counts.get("candidate", 0),
        attendance_rate=round(rate, 4),
        finalized=finalized,
    )


def _summarize_student(
    student_id: str,
    records: list[AttendanceFinalizedRecord],
) -> AttendanceStudentReport:
    lecture_ids = {record.lecture_id for record in records if record.lecture_id}
    confirmed = sum(1 for record in records if record.attendance_state == "confirmed")
    lectures = len(lecture_ids) if lecture_ids else (1 if records else 0)
    rate = (confirmed / lectures) if lectures else 0.0

    return AttendanceStudentReport(
        student_id=student_id,
        lectures=lectures,
        confirmed=confirmed,
        manual_review=sum(1 for r in records if r.attendance_state == "manual_review"),
        insufficient_presence=sum(1 for r in records if r.attendance_state == "insufficient_presence"),
        expired=sum(1 for r in records if r.attendance_state == "expired"),
        attendance_rate=round(rate, 4),
    )


def get_report_service() -> AttendanceReportService:
    return AttendanceReportService()
