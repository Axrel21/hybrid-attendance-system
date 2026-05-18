"""RecognitionIngestor — validates inbound events and routes to AttendanceEngine.

Responsibilities:
- Resolve the active lecture (status = active_window_open).
- Map gallery_identity → Student.
- Validate the student is enrolled in the active lecture's subject.
- Persist every event to recognition_event_log (even rejected ones).
- Delegate state transitions exclusively to AttendanceEngine.
- Return a structured IngestionResult to the caller.

This module has no knowledge of HTTP; the FastAPI route owns session commit.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cloud_backend.attendance.engine import AttendanceEngine, TransitionResult
from cloud_backend.models.attendance_record import AttendanceRecord
from cloud_backend.models.enrollment import Enrollment
from cloud_backend.models.lecture import Lecture
from cloud_backend.models.recognition_event_log import RecognitionEventLog
from cloud_backend.models.student import Student
from cloud_backend.sessions.lifecycle import LectureStatus


# ---------------------------------------------------------------------------
# Disposition tags (written to recognition_event_log and IngestionResult)
# ---------------------------------------------------------------------------

class Disposition:
    TRANSITIONED    = "transitioned"
    ACCEPTED        = "accepted"        # event counted; threshold not yet met
    NO_ACTIVE_LECTURE = "no_active_lecture"
    UNKNOWN_IDENTITY  = "unknown_identity"
    NOT_ENROLLED      = "not_enrolled"
    WINDOW_CLOSED     = "window_closed"
    SUPPRESSED        = "suppressed"    # cooldown / already confirmed
    ENGINE_SKIP       = "engine_skip"


# ---------------------------------------------------------------------------
# Result returned from the ingestor
# ---------------------------------------------------------------------------

@dataclass
class IngestorResult:
    accepted: bool
    disposition: str
    gallery_identity: str
    lecture_id: uuid.UUID | None = None
    record_id: uuid.UUID | None = None
    from_state: str | None = None
    to_state: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Ingestor
# ---------------------------------------------------------------------------

class RecognitionIngestor:
    """Validate and route a single RecognitionEvent."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._engine = AttendanceEngine(session)

    async def ingest(
        self,
        *,
        gallery_identity: str,
        confidence: float,
        source: str,
        timestamp_ms: int | None,
        meta: dict[str, Any] | None = None,
    ) -> IngestorResult:
        """Process one recognition event end-to-end within the caller's session."""
        meta = meta or {}

        # 1. Resolve active lecture ----------------------------------------
        lecture = await self._find_active_lecture()
        if lecture is None:
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=None,
                meta=meta,
            )
            return IngestorResult(
                accepted=False,
                disposition=Disposition.NO_ACTIVE_LECTURE,
                gallery_identity=gallery_identity,
                detail="no lecture with status active_window_open found",
            )

        # 2. Map gallery_identity → Student --------------------------------
        student = await self._resolve_student(gallery_identity)
        if student is None:
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=lecture.id,
                meta=meta,
            )
            return IngestorResult(
                accepted=False,
                disposition=Disposition.UNKNOWN_IDENTITY,
                gallery_identity=gallery_identity,
                lecture_id=lecture.id,
                detail=f"gallery_identity {gallery_identity!r} not mapped to any student",
            )

        # 3. Confirm enrollment in this lecture's subject ------------------
        enrolled = await self._is_enrolled(student.id, lecture.subject_id)
        if not enrolled:
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=lecture.id,
                meta=meta,
            )
            return IngestorResult(
                accepted=False,
                disposition=Disposition.NOT_ENROLLED,
                gallery_identity=gallery_identity,
                lecture_id=lecture.id,
                detail=f"student {student.id} not actively enrolled in subject {lecture.subject_id}",
            )

        # 4. Resolve AttendanceRecord for (lecture, student) ---------------
        record = await self._find_record(lecture.id, student.id)
        if record is None:
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=lecture.id,
                meta=meta,
            )
            return IngestorResult(
                accepted=False,
                disposition=Disposition.ENGINE_SKIP,
                gallery_identity=gallery_identity,
                lecture_id=lecture.id,
                detail="attendance record not initialised for this student/lecture pair",
            )

        # 5. Always log the raw event before engine processing -------------
        await self._log_event(
            gallery_identity=gallery_identity,
            confidence=confidence,
            source=source,
            timestamp_ms=timestamp_ms,
            lecture_id=lecture.id,
            meta={**meta, "student_id": str(student.id), "record_id": str(record.id)},
        )

        # 6. Delegate to AttendanceEngine ----------------------------------
        result: TransitionResult = await self._engine.process_recognition_event(
            record_id=record.id,
            lecture_status=lecture.status,
            confidence=confidence,
            source=source,
            timestamp_ms=timestamp_ms,
            meta=meta,
        )

        if result.accepted and result.from_state != result.to_state:
            disposition = Disposition.TRANSITIONED
        elif result.accepted:
            disposition = Disposition.ACCEPTED
        else:
            disposition = Disposition.SUPPRESSED

        return IngestorResult(
            accepted=result.accepted,
            disposition=disposition,
            gallery_identity=gallery_identity,
            lecture_id=lecture.id,
            record_id=record.id,
            from_state=result.from_state,
            to_state=result.to_state,
            detail=result.reason,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _find_active_lecture(self) -> Lecture | None:
        """Return the most-recently-started open lecture, or None."""
        stmt = (
            select(Lecture)
            .where(Lecture.status == LectureStatus.ACTIVE_WINDOW_OPEN.value)
            .order_by(Lecture.actual_start.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_student(self, gallery_identity: str) -> Student | None:
        stmt = select(Student).where(Student.gallery_identity == gallery_identity)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _is_enrolled(
        self, student_id: uuid.UUID, subject_id: uuid.UUID
    ) -> bool:
        stmt = select(Enrollment.id).where(
            Enrollment.student_id == student_id,
            Enrollment.subject_id == subject_id,
            Enrollment.active.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _find_record(
        self, lecture_id: uuid.UUID, student_id: uuid.UUID
    ) -> AttendanceRecord | None:
        stmt = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.lecture_id == lecture_id,
                AttendanceRecord.student_id == student_id,
            )
            .options(selectinload(AttendanceRecord.events))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _log_event(
        self,
        *,
        gallery_identity: str,
        confidence: float,
        source: str,
        timestamp_ms: int | None,
        lecture_id: uuid.UUID | None,
        meta: dict[str, Any],
    ) -> None:
        entry = RecognitionEventLog(
            lecture_id=lecture_id,
            gallery_identity=gallery_identity,
            confidence=confidence,
            source=source,
            timestamp_ms=timestamp_ms,
            meta_json=json.dumps(meta) if meta else None,
        )
        self._session.add(entry)
        await self._session.flush()
