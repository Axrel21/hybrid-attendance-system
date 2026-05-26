"""Read-only queries for attendance evidence correlation (D4 Track 1)."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import os

from cloud_backend.models.camera_source import CameraSource
from cloud_backend.models.lecture import Lecture
from cloud_backend.models.recognition_event_log import RecognitionEventLog
from cloud_backend.models.student import Student
from cloud_backend.attendance.presence_timeline import PresenceSession


async def fetch_recognition_logs_for_evidence(
    session: AsyncSession,
    *,
    lecture_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[RecognitionEventLog]:
    stmt = (
        select(RecognitionEventLog)
        .order_by(RecognitionEventLog.received_at.desc())
        .limit(limit)
    )
    if lecture_id is not None:
        stmt = stmt.where(RecognitionEventLog.lecture_id == lecture_id)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def resolve_classroom_id_for_log(
    session: AsyncSession,
    entry: RecognitionEventLog,
) -> uuid.UUID | None:
    if entry.classroom_id is not None:
        return entry.classroom_id
    if entry.lecture_id is not None:
        lecture = await session.get(Lecture, entry.lecture_id)
        if lecture is not None:
            return lecture.classroom_id
    if not entry.camera_id:
        return None
    stmt = select(CameraSource).where(CameraSource.camera_id == entry.camera_id)
    result = await session.execute(stmt)
    source = result.scalar_one_or_none()
    if source is None:
        return None
    return source.classroom_id


def surveillance_camera_ids_for_classroom(
    classroom_id: uuid.UUID,
    surv_by_classroom: dict[uuid.UUID, list[str]],
    presence_sessions: list[PresenceSession],
) -> list[str]:
    """Registry cameras first; optional fallback to live presence camera_ids."""
    registered = list(surv_by_classroom.get(classroom_id, []))
    if registered:
        return registered

    if os.environ.get("EVIDENCE_PRESENCE_CAMERA_FALLBACK", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return []

    explicit = os.environ.get("EVIDENCE_SURVEILLANCE_CAMERA_IDS", "").strip()
    if explicit:
        return [camera.strip() for camera in explicit.split(",") if camera.strip()]

    return list(
        {
            session.camera_id
            for session in presence_sessions
            if session.track_id > 0 and session.camera_id
        }
    )


def matching_presence_sessions(
    classroom_id: uuid.UUID,
    surv_by_classroom: dict[uuid.UUID, list[str]],
    presence_sessions: list[PresenceSession],
) -> list[PresenceSession]:
    camera_ids = set(
        surveillance_camera_ids_for_classroom(
            classroom_id,
            surv_by_classroom,
            presence_sessions,
        )
    )
    if not camera_ids:
        return []

    return [
        session
        for session in presence_sessions
        if session.track_id > 0 and session.camera_id in camera_ids
    ]


async def resolve_student_id(
    session: AsyncSession,
    gallery_identity: str,
) -> str:
    stmt = select(Student).where(Student.gallery_identity == gallery_identity)
    result = await session.execute(stmt)
    student = result.scalar_one_or_none()
    if student is not None:
        return str(student.id)
    return gallery_identity


async def surveillance_cameras_by_classroom(
    session: AsyncSession,
) -> dict[uuid.UUID, list[str]]:
    """Map classroom_id → surveillance camera_id list (registry meta or naming)."""
    result = await session.execute(select(CameraSource).where(CameraSource.active.is_(True)))
    sources = list(result.scalars().all())

    mapping: dict[uuid.UUID, list[str]] = {}
    for source in sources:
        if not _is_surveillance_source(source):
            continue
        mapping.setdefault(source.classroom_id, []).append(source.camera_id)
    return mapping


def _is_surveillance_source(source: CameraSource) -> bool:
    if source.meta_json:
        try:
            meta = json.loads(source.meta_json)
        except json.JSONDecodeError:
            meta = {}
        role = str(meta.get("role", "")).lower()
        if role == "surveillance":
            return True
    camera_id = (source.camera_id or "").lower()
    return camera_id.startswith("surv") or "surveillance" in camera_id
