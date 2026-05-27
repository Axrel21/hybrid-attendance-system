"""RecognitionIngestor — validates inbound events and routes to AttendanceEngine.

Resolution pipeline (D.2A):
  camera_id / classroom_id → classroom → active lecture in classroom
  → enrollment validation → AttendanceEngine

When neither camera_id nor classroom_id is supplied, falls back to the D.1
global active-lecture lookup for backward compatibility.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cloud_backend.attendance.engine import AttendanceEngine, TransitionResult
from cloud_backend.classroom.resolver import resolve_active_lecture, resolve_classroom
from cloud_backend.models.attendance_record import AttendanceRecord
from cloud_backend.models.enrollment import Enrollment
from cloud_backend.models.lecture import Lecture
from cloud_backend.models.recognition_event_log import RecognitionEventLog
from cloud_backend.models.student import Student


class Disposition:
    TRANSITIONED = "transitioned"
    ACCEPTED = "accepted"
    NO_ACTIVE_LECTURE = "no_active_lecture"
    NO_ACTIVE_LECTURE_IN_CLASSROOM = "no_active_lecture_in_classroom"
    UNKNOWN_CAMERA = "unknown_camera"
    UNKNOWN_CLASSROOM = "unknown_classroom"
    UNKNOWN_IDENTITY = "unknown_identity"
    NOT_ENROLLED = "not_enrolled"
    WINDOW_CLOSED = "window_closed"
    SUPPRESSED = "suppressed"
    ENGINE_SKIP = "engine_skip"


@dataclass
class IngestorResult:
    accepted: bool
    disposition: str
    gallery_identity: str
    lecture_id: uuid.UUID | None = None
    classroom_id: uuid.UUID | None = None
    camera_id: str | None = None
    record_id: uuid.UUID | None = None
    from_state: str | None = None
    to_state: str | None = None
    detail: str | None = None


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
        classroom_id: uuid.UUID | None = None,
        camera_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> IngestorResult:
        """Process one recognition event end-to-end within the caller's session."""
        meta = meta or {}

        # 1. Resolve classroom context -------------------------------------
        classroom_resolution = await resolve_classroom(
            self._session,
            classroom_id=classroom_id,
            camera_id=camera_id,
        )

        resolved_classroom_id = classroom_resolution.classroom_id
        resolved_camera_id = classroom_resolution.camera_id

        if classroom_resolution.error:
            disposition = (
                Disposition.UNKNOWN_CAMERA
                if camera_id
                else Disposition.UNKNOWN_CLASSROOM
            )
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=None,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                meta={**meta, "disposition": disposition},
            )
            return IngestorResult(
                accepted=False,
                disposition=disposition,
                gallery_identity=gallery_identity,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                detail=classroom_resolution.error,
            )

        # 2. Resolve active lecture in classroom (or global fallback) ------
        lecture_resolution = await resolve_active_lecture(
            self._session,
            classroom_resolution,
        )
        lecture = lecture_resolution.lecture

        if lecture is None:
            disposition = (
                Disposition.NO_ACTIVE_LECTURE_IN_CLASSROOM
                if lecture_resolution.scoped
                else Disposition.NO_ACTIVE_LECTURE
            )
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=None,
                classroom_id=lecture_resolution.classroom_id or resolved_classroom_id,
                camera_id=resolved_camera_id,
                meta={**meta, "disposition": disposition},
            )
            return IngestorResult(
                accepted=False,
                disposition=disposition,
                gallery_identity=gallery_identity,
                classroom_id=lecture_resolution.classroom_id or resolved_classroom_id,
                camera_id=resolved_camera_id,
                detail=lecture_resolution.detail,
            )

        resolved_classroom_id = lecture.classroom_id

        # 3. Map gallery_identity → Student --------------------------------
        student = await self._resolve_student(gallery_identity)
        if student is None:
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=lecture.id,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                meta={**meta, "disposition": Disposition.UNKNOWN_IDENTITY},
            )
            return IngestorResult(
                accepted=False,
                disposition=Disposition.UNKNOWN_IDENTITY,
                gallery_identity=gallery_identity,
                lecture_id=lecture.id,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                detail=f"gallery_identity {gallery_identity!r} not mapped to any student",
            )

        # 4. Confirm enrollment in this lecture's subject ------------------
        enrolled = await self._is_enrolled(student.id, lecture.subject_id)
        if not enrolled:
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=lecture.id,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                meta={**meta, "disposition": Disposition.NOT_ENROLLED},
            )
            return IngestorResult(
                accepted=False,
                disposition=Disposition.NOT_ENROLLED,
                gallery_identity=gallery_identity,
                lecture_id=lecture.id,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                detail=f"student {student.id} not actively enrolled in subject {lecture.subject_id}",
            )

        # 5. Resolve AttendanceRecord for (lecture, student) ---------------
        record = await self._find_record(lecture.id, student.id)
        if record is None:
            await self._log_event(
                gallery_identity=gallery_identity,
                confidence=confidence,
                source=source,
                timestamp_ms=timestamp_ms,
                lecture_id=lecture.id,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                meta={**meta, "disposition": Disposition.ENGINE_SKIP},
            )
            return IngestorResult(
                accepted=False,
                disposition=Disposition.ENGINE_SKIP,
                gallery_identity=gallery_identity,
                lecture_id=lecture.id,
                classroom_id=resolved_classroom_id,
                camera_id=resolved_camera_id,
                detail="attendance record not initialised for this student/lecture pair",
            )

        # 6. Log raw event before engine processing ------------------------
        await self._log_event(
            gallery_identity=gallery_identity,
            confidence=confidence,
            source=source,
            timestamp_ms=timestamp_ms,
            lecture_id=lecture.id,
            classroom_id=resolved_classroom_id,
            camera_id=resolved_camera_id,
            meta={
                **meta,
                "student_id": str(student.id),
                "record_id": str(record.id),
            },
        )

        # 7. Delegate to AttendanceEngine ----------------------------------
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
            classroom_id=resolved_classroom_id,
            camera_id=resolved_camera_id,
            record_id=record.id,
            from_state=result.from_state,
            to_state=result.to_state,
            detail=result.reason,
        )

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
        classroom_id: uuid.UUID | None,
        camera_id: str | None,
        meta: dict[str, Any],
    ) -> None:
        entry = RecognitionEventLog(
            lecture_id=lecture_id,
            classroom_id=classroom_id,
            camera_id=camera_id,
            gallery_identity=gallery_identity,
            confidence=confidence,
            source=source,
            timestamp_ms=timestamp_ms,
            meta_json=json.dumps(meta) if meta else None,
        )
        self._session.add(entry)
        await self._session.flush()
