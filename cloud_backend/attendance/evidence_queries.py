"""Read-only queries for attendance evidence correlation (D4 Track 1)."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.models.camera_source import CameraSource
from cloud_backend.models.recognition_event_log import RecognitionEventLog
from cloud_backend.models.student import Student


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
    if not entry.camera_id:
        return None
    stmt = select(CameraSource).where(CameraSource.camera_id == entry.camera_id)
    result = await session.execute(stmt)
    source = result.scalar_one_or_none()
    if source is None:
        return None
    return source.classroom_id


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
