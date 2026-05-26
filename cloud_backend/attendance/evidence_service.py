"""AttendanceEvidenceService — correlate recognition with presence (D4 Track 4)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.evidence_queries import (
    fetch_recognition_logs_for_evidence,
    matching_presence_sessions,
    resolve_classroom_id_for_log,
    resolve_student_id,
    surveillance_cameras_by_classroom,
)
from cloud_backend.attendance.evidence_store import get_evidence_store
from cloud_backend.attendance.presence_timeline import get_timeline_service
from cloud_backend.attendance.schemas.evidence import AttendanceEvidenceRecord
from cloud_backend.attendance.temporal_scorer import get_temporal_scorer

log = logging.getLogger("cloud_backend.attendance.evidence")


class AttendanceEvidenceService:
    """Build evidence records from recognition history and presence sessions.

    Does not call AttendanceEngine or modify attendance state.
    """

    def __init__(self) -> None:
        self._temporal = get_temporal_scorer()

    async def build_records(
        self,
        session: AsyncSession,
        *,
        lecture_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[AttendanceEvidenceRecord]:
        logs = await fetch_recognition_logs_for_evidence(
            session,
            lecture_id=lecture_id,
            limit=limit,
        )
        surv_by_classroom = await surveillance_cameras_by_classroom(session)
        presence_sessions = get_timeline_service().list_sessions()

        records: list[AttendanceEvidenceRecord] = []
        for entry in logs:
            record = await self._correlate_entry(
                session,
                entry,
                surv_by_classroom=surv_by_classroom,
                presence_sessions=presence_sessions,
            )
            records.append(record)

        get_evidence_store().replace(records)
        log.info("attendance evidence built total=%d lecture_id=%s", len(records), lecture_id)
        return records

    async def _correlate_entry(
        self,
        session: AsyncSession,
        entry,
        *,
        surv_by_classroom: dict[uuid.UUID, list[str]],
        presence_sessions: list,
    ) -> AttendanceEvidenceRecord:
        gallery_identity = (entry.gallery_identity or "").strip()
        recognized_at = _recognized_at_ms(entry)
        lecture_key = str(entry.lecture_id) if entry.lecture_id else None
        classroom_id = await resolve_classroom_id_for_log(session, entry)
        classroom_key = str(classroom_id) if classroom_id else None

        if not gallery_identity:
            return AttendanceEvidenceRecord(
                student_id="unknown",
                evidence="unknown",
                confidence="low",
                recognized_at=recognized_at,
                camera_id=entry.camera_id,
                lecture_id=lecture_key,
                classroom_id=classroom_key,
            )

        student_id = await resolve_student_id(session, gallery_identity)

        matching_sessions: list = []
        if classroom_id is not None:
            matching_sessions = matching_presence_sessions(
                classroom_id,
                surv_by_classroom,
                presence_sessions,
            )

        if matching_sessions:
            session_pick = self._temporal.pick_session(matching_sessions, recognized_at)
            time_delta_sec, confidence = self._temporal.score(
                recognized_at_ms=recognized_at,
                presence_first_seen_ms=session_pick.first_seen,
            )
            return AttendanceEvidenceRecord(
                student_id=student_id,
                evidence="presence_observed",
                confidence=confidence,
                recognized_at=recognized_at,
                camera_id=entry.camera_id,
                lecture_id=lecture_key,
                classroom_id=classroom_key,
                presence_camera_id=session_pick.camera_id,
                presence_track_id=session_pick.track_id,
                presence_duration_sec=session_pick.duration_sec,
                time_delta_sec=time_delta_sec,
            )

        return AttendanceEvidenceRecord(
            student_id=student_id,
            evidence="recognized_only",
            confidence="low",
            recognized_at=recognized_at,
            camera_id=entry.camera_id,
            lecture_id=lecture_key,
            classroom_id=classroom_key,
        )


def _recognized_at_ms(entry) -> int:
    if entry.timestamp_ms is not None:
        return int(entry.timestamp_ms)
    if entry.received_at is not None:
        return int(entry.received_at.timestamp() * 1000)
    return 0


def get_evidence_service() -> AttendanceEvidenceService:
    return AttendanceEvidenceService()
