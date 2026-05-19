"""Classroom-aware lecture resolution for recognition ingestion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cloud_backend.models.camera_source import CameraSource
from cloud_backend.models.classroom import Classroom
from cloud_backend.models.lecture import Lecture
from cloud_backend.sessions.lifecycle import LectureStatus


@dataclass(frozen=True)
class ClassroomResolution:
    """Result of resolving a recognition event to a classroom context."""

    classroom_id: uuid.UUID | None
    camera_id: str | None = None
    use_global_fallback: bool = False
    error: str | None = None


@dataclass(frozen=True)
class LectureResolution:
    """Result of resolving an active lecture within a classroom."""

    lecture: Lecture | None
    classroom_id: uuid.UUID | None = None
    scoped: bool = False
    detail: str | None = None


async def resolve_classroom(
    session: AsyncSession,
    *,
    classroom_id: uuid.UUID | None = None,
    camera_id: str | None = None,
) -> ClassroomResolution:
    """Map camera_id or classroom_id to a classroom.

    When neither is supplied, signals global D.1 fallback (single active lecture).
    """
    if camera_id:
        stmt = select(CameraSource).where(CameraSource.camera_id == camera_id)
        result = await session.execute(stmt)
        source = result.scalar_one_or_none()
        if source is None:
            return ClassroomResolution(
                classroom_id=None,
                camera_id=camera_id,
                error=f"unknown camera_id {camera_id!r}",
            )
        if not source.active:
            return ClassroomResolution(
                classroom_id=None,
                camera_id=camera_id,
                error=f"camera {camera_id!r} is inactive",
            )
        return ClassroomResolution(
            classroom_id=source.classroom_id,
            camera_id=camera_id,
        )

    if classroom_id is not None:
        classroom = await session.get(Classroom, classroom_id)
        if classroom is None:
            return ClassroomResolution(
                classroom_id=None,
                error=f"unknown classroom_id {classroom_id}",
            )
        return ClassroomResolution(classroom_id=classroom_id)

    return ClassroomResolution(classroom_id=None, use_global_fallback=True)


async def resolve_active_lecture(
    session: AsyncSession,
    resolution: ClassroomResolution,
) -> LectureResolution:
    """Find the active lecture for a resolved classroom, or fall back globally."""
    if resolution.error:
        return LectureResolution(
            lecture=None,
            classroom_id=resolution.classroom_id,
            detail=resolution.error,
        )

    if resolution.classroom_id is not None:
        lecture = await _active_lecture_for_classroom(session, resolution.classroom_id)
        if lecture is None:
            return LectureResolution(
                lecture=None,
                classroom_id=resolution.classroom_id,
                scoped=True,
                detail="no active lecture in classroom",
            )
        return LectureResolution(
            lecture=lecture,
            classroom_id=resolution.classroom_id,
            scoped=True,
        )

    if resolution.use_global_fallback:
        lecture = await _active_lecture_global(session)
        if lecture is None:
            return LectureResolution(
                lecture=None,
                scoped=False,
                detail="no lecture with status active_window_open found",
            )
        return LectureResolution(
            lecture=lecture,
            classroom_id=lecture.classroom_id,
            scoped=False,
        )

    return LectureResolution(lecture=None, detail="classroom could not be resolved")


async def _active_lecture_for_classroom(
    session: AsyncSession,
    classroom_id: uuid.UUID,
) -> Lecture | None:
    stmt = (
        select(Lecture)
        .where(
            Lecture.classroom_id == classroom_id,
            Lecture.status == LectureStatus.ACTIVE_WINDOW_OPEN.value,
        )
        .options(
            selectinload(Lecture.subject),
            selectinload(Lecture.classroom),
        )
        .order_by(Lecture.actual_start.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _active_lecture_global(session: AsyncSession) -> Lecture | None:
    stmt = (
        select(Lecture)
        .where(Lecture.status == LectureStatus.ACTIVE_WINDOW_OPEN.value)
        .options(
            selectinload(Lecture.subject),
            selectinload(Lecture.classroom),
        )
        .order_by(Lecture.actual_start.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_all_active_lectures(
    session: AsyncSession,
    *,
    classroom_id: uuid.UUID | None = None,
) -> list[Lecture]:
    """Return all active-window-open lectures, optionally filtered by classroom."""
    stmt = (
        select(Lecture)
        .where(Lecture.status == LectureStatus.ACTIVE_WINDOW_OPEN.value)
        .options(
            selectinload(Lecture.subject),
            selectinload(Lecture.classroom),
            selectinload(Lecture.attendance_records),
        )
        .order_by(Lecture.actual_start.desc())
    )
    if classroom_id is not None:
        stmt = stmt.where(Lecture.classroom_id == classroom_id)

    result = await session.execute(stmt)
    return list(result.scalars().all())
