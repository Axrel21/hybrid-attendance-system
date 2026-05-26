"""AttendanceEvidenceService — correlate recognition with presence (D4 Track 1)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.evidence_queries import (
    fetch_recognition_logs_for_evidence,
    resolve_classroom_id_for_log,
    resolve_student_id,
    surveillance_cameras_by_classroom,
)
from cloud_backend.attendance.evidence_store import get_evidence_store
from cloud_backend.attendance.presence_timeline import get_timeline_service
from cloud_backend.attendance.schemas.evidence import AttendanceEvidenceRecord

log = logging.getLogger("cloud_backend.attendance.evidence")


class AttendanceEvidenceService:
    """Build evidence records from recognition history and presence sessions.

    Does not call AttendanceEngine or modify attendance state.
    """

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
        if not gallery_identity:
            return AttendanceEvidenceRecord(
                student_id="unknown",
                evidence="unknown",
                confidence="low",
                recognized_at=_recognized_at_ms(entry),
                camera_id=entry.camera_id,
                lecture_id=str(entry.lecture_id) if entry.lecture_id else None,
                classroom_id=str(entry.classroom_id) if entry.classroom_id else None,
            )

        student_id = await resolve_student_id(session, gallery_identity)
        classroom_id = await resolve_classroom_id_for_log(session, entry)

        surv_cameras: list[str] = []
        if classroom_id is not None:
            surv_cameras = surv_by_classroom.get(classroom_id, [])

        matching_sessions = [
            s
            for s in presence_sessions
            if s.camera_id in surv_cameras
        ]

        recognized_at = _recognized_at_ms(entry)

        if matching_sessions:
            session_pick = _pick_session(matching_sessions, recognized_at)
            confidence = _confidence_for_presence(entry.confidence, session_pick)
            return AttendanceEvidenceRecord(
                student_id=student_id,
                evidence="presence_observed",
                confidence=confidence,
                recognized_at=recognized_at,
                camera_id=entry.camera_id,
                lecture_id=str(entry.lecture_id) if entry.lecture_id else None,
                classroom_id=str(classroom_id) if classroom_id else None,
                presence_camera_id=session_pick.camera_id,
                presence_track_id=session_pick.track_id,
            )

        return AttendanceEvidenceRecord(
            student_id=student_id,
            evidence="recognized_only",
            confidence="low",
            recognized_at=recognized_at,
            camera_id=entry.camera_id,
            lecture_id=str(entry.lecture_id) if entry.lecture_id else None,
            classroom_id=str(classroom_id) if classroom_id else None,
        )


def _recognized_at_ms(entry) -> int:
    if entry.timestamp_ms is not None:
        return int(entry.timestamp_ms)
    if entry.received_at is not None:
        return int(entry.received_at.timestamp() * 1000)
    return 0


def _pick_session(sessions: list, recognized_at_ms: int):
    for session in sessions:
        if session.first_seen <= recognized_at_ms <= session.last_seen:
            return session
    active = [s for s in sessions if s.status == "active"]
    if active:
        return active[0]
    return sessions[0]


def _confidence_for_presence(recognition_confidence: float, session) -> str:
    if session.status == "active" and recognition_confidence >= 0.7:
        return "high"
    if session.status == "active":
        return "medium"
    return "medium"


def get_evidence_service() -> AttendanceEvidenceService:
    return AttendanceEvidenceService()
