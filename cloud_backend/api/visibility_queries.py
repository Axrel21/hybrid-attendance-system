"""Read-only queries for operational visibility — no writes, no engine calls."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cloud_backend.classroom.resolver import fetch_all_active_lectures
from cloud_backend.models.attendance_event import AttendanceEvent
from cloud_backend.models.attendance_record import AttendanceRecord
from cloud_backend.models.camera_source import CameraSource
from cloud_backend.models.classroom import Classroom
from cloud_backend.models.lecture import Lecture
from cloud_backend.models.recognition_event_log import RecognitionEventLog
from cloud_backend.models.student import Student
from cloud_backend.sessions.exceptions import LectureNotFoundError


def _parse_meta(meta_json: str | None) -> dict:
    if not meta_json:
        return {}
    try:
        return json.loads(meta_json)
    except json.JSONDecodeError:
        return {}


def _infer_recognition_outcome(
    *,
    lecture_id: uuid.UUID | None,
    meta: dict,
    matched_engine_event: bool,
) -> tuple[bool, str]:
    disposition = meta.get("disposition")
    if isinstance(disposition, str):
        if disposition in ("transitioned", "accepted"):
            return True, disposition
        if disposition in (
            "no_active_lecture",
            "no_active_lecture_in_classroom",
            "unknown_camera",
            "unknown_classroom",
            "unknown_identity",
            "not_enrolled",
            "engine_skip",
            "suppressed",
            "window_closed",
        ):
            return False, disposition

    if lecture_id is None:
        return False, "no_active_lecture"
    if not meta.get("record_id"):
        return False, "rejected_pre_engine"
    if matched_engine_event:
        return True, "accepted"
    return False, "suppressed"


async def get_lecture_or_404(session: AsyncSession, lecture_id: uuid.UUID) -> Lecture:
    stmt = (
        select(Lecture)
        .where(Lecture.id == lecture_id)
        .options(
            selectinload(Lecture.subject),
            selectinload(Lecture.classroom),
        )
    )
    result = await session.execute(stmt)
    lecture = result.scalar_one_or_none()
    if lecture is None:
        raise LectureNotFoundError(str(lecture_id))
    return lecture


async def fetch_lecture_records(
    session: AsyncSession,
    lecture_id: uuid.UUID,
) -> list[tuple[AttendanceRecord, Student]]:
    await get_lecture_or_404(session, lecture_id)

    stmt = (
        select(AttendanceRecord, Student)
        .join(Student, AttendanceRecord.student_id == Student.id)
        .where(AttendanceRecord.lecture_id == lecture_id)
        .options(selectinload(AttendanceRecord.events))
        .order_by(Student.name.asc())
    )
    result = await session.execute(stmt)
    return list(result.all())


async def fetch_lecture_events(
    session: AsyncSession,
    lecture_id: uuid.UUID,
) -> list[tuple[AttendanceEvent, AttendanceRecord, Student]]:
    await get_lecture_or_404(session, lecture_id)

    stmt = (
        select(AttendanceEvent, AttendanceRecord, Student)
        .join(AttendanceRecord, AttendanceEvent.attendance_record_id == AttendanceRecord.id)
        .join(Student, AttendanceRecord.student_id == Student.id)
        .where(AttendanceRecord.lecture_id == lecture_id)
        .order_by(AttendanceEvent.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.all())


async def fetch_recognition_logs(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
    classroom_id: uuid.UUID | None = None,
    camera_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[RecognitionEventLog]:
    stmt = (
        select(RecognitionEventLog)
        .order_by(RecognitionEventLog.received_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if lecture_id is not None:
        stmt = stmt.where(RecognitionEventLog.lecture_id == lecture_id)
    if classroom_id is not None:
        stmt = stmt.where(RecognitionEventLog.classroom_id == classroom_id)
    if camera_id is not None:
        stmt = stmt.where(RecognitionEventLog.camera_id == camera_id)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_recognition_logs(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
    classroom_id: uuid.UUID | None = None,
    camera_id: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(RecognitionEventLog)
    if lecture_id is not None:
        stmt = stmt.where(RecognitionEventLog.lecture_id == lecture_id)
    if classroom_id is not None:
        stmt = stmt.where(RecognitionEventLog.classroom_id == classroom_id)
    if camera_id is not None:
        stmt = stmt.where(RecognitionEventLog.camera_id == camera_id)
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def recognition_logs_matched_engine(
    session: AsyncSession,
    entries: list[RecognitionEventLog],
) -> dict[uuid.UUID, bool]:
    """Map recognition log id → whether an AttendanceEvent was written near ingestion."""
    matched: dict[uuid.UUID, bool] = {entry.id: False for entry in entries}
    if not entries:
        return matched

    lookups: list[tuple[uuid.UUID, uuid.UUID, datetime, datetime]] = []
    for entry in entries:
        meta = _parse_meta(entry.meta_json)
        raw_record_id = meta.get("record_id")
        if not raw_record_id:
            continue
        try:
            record_id = uuid.UUID(str(raw_record_id))
        except ValueError:
            continue
        received = entry.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        lookups.append(
            (
                entry.id,
                record_id,
                received - timedelta(seconds=2),
                received + timedelta(seconds=30),
            )
        )

    if not lookups:
        return matched

    record_ids = {record_id for _, record_id, _, _ in lookups}
    stmt = select(
        AttendanceEvent.attendance_record_id,
        AttendanceEvent.created_at,
    ).where(AttendanceEvent.attendance_record_id.in_(record_ids))
    result = await session.execute(stmt)
    engine_events = list(result.all())

    for log_id, record_id, start, end in lookups:
        for arid, created_at in engine_events:
            if arid != record_id:
                continue
            ts = created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if start <= ts <= end:
                matched[log_id] = True
                break

    return matched


async def fetch_active_lecture(
    session: AsyncSession,
    *,
    classroom_id: uuid.UUID | None = None,
) -> Lecture | None:
    lectures = await fetch_all_active_lectures(session, classroom_id=classroom_id)
    return lectures[0] if lectures else None


async def fetch_camera_sources(
    session: AsyncSession,
    *,
    classroom_id: uuid.UUID | None = None,
    active_only: bool = True,
) -> list[tuple[CameraSource, Classroom]]:
    stmt = (
        select(CameraSource, Classroom)
        .join(Classroom, CameraSource.classroom_id == Classroom.id)
        .order_by(CameraSource.camera_id.asc())
    )
    if classroom_id is not None:
        stmt = stmt.where(CameraSource.classroom_id == classroom_id)
    if active_only:
        stmt = stmt.where(CameraSource.active.is_(True))

    result = await session.execute(stmt)
    return list(result.all())
