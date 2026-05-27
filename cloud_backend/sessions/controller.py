"""Lecture session controller — lifecycle orchestration without attendance decisions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cloud_backend.attendance.state_machine import AttendanceState
from cloud_backend.models.attendance_record import AttendanceRecord
from cloud_backend.models.classroom import Classroom
from cloud_backend.models.enrollment import Enrollment
from cloud_backend.models.lecture import Lecture
from cloud_backend.models.subject import Subject
from cloud_backend.sessions.exceptions import EntityNotFoundError, LectureLifecycleError, LectureNotFoundError
from cloud_backend.sessions.lifecycle import LectureStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LectureSessionController:
    """Manage lecture lifecycle transitions and attendance-record initialization."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_lecture(
        self,
        *,
        subject_id: uuid.UUID,
        classroom_id: uuid.UUID,
        scheduled_start: datetime,
        scheduled_end: datetime,
        attendance_window_minutes: int = 15,
    ) -> Lecture:
        if scheduled_end <= scheduled_start:
            raise LectureLifecycleError(
                "scheduled_end must be after scheduled_start",
                status_code=422,
            )

        subject = await self._session.get(Subject, subject_id)
        if subject is None:
            raise EntityNotFoundError("subject", str(subject_id))

        classroom = await self._session.get(Classroom, classroom_id)
        if classroom is None:
            raise EntityNotFoundError("classroom", str(classroom_id))

        lecture = Lecture(
            subject_id=subject_id,
            classroom_id=classroom_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            attendance_window_minutes=attendance_window_minutes,
            status=LectureStatus.SCHEDULED.value,
        )
        self._session.add(lecture)
        await self._session.flush()
        return await self._load_lecture(lecture.id)

    async def start_lecture(self, lecture_id: uuid.UUID) -> Lecture:
        lecture = await self._get_mutable_lecture(lecture_id)
        self._require_status(lecture, {LectureStatus.SCHEDULED}, action="start")

        student_ids = await self._active_enrolled_student_ids(lecture.subject_id)
        if student_ids:
            await self._session.execute(
                insert(AttendanceRecord),
                [
                    {
                        "id": uuid.uuid4(),
                        "lecture_id": lecture.id,
                        "student_id": student_id,
                        "state": AttendanceState.UNDETECTED.value,
                        "is_locked": False,
                    }
                    for student_id in student_ids
                ],
            )

        lecture.status = LectureStatus.ACTIVE_WINDOW_OPEN.value
        lecture.actual_start = _utcnow()
        await self._session.flush()
        return await self._load_lecture(lecture.id)

    async def close_lecture(self, lecture_id: uuid.UUID) -> Lecture:
        lecture = await self._get_mutable_lecture(lecture_id)
        self._require_status(
            lecture,
            {LectureStatus.ACTIVE_WINDOW_OPEN},
            action="close",
        )

        lecture.status = LectureStatus.ACTIVE_WINDOW_CLOSED.value
        lecture.actual_end = _utcnow()
        await self._session.flush()
        return await self._load_lecture(lecture.id)

    async def finalize_lecture(self, lecture_id: uuid.UUID) -> Lecture:
        lecture = await self._get_mutable_lecture(lecture_id)
        self._require_status(
            lecture,
            {LectureStatus.ACTIVE_WINDOW_CLOSED},
            action="finalize",
        )

        lecture.status = LectureStatus.FINALIZED.value
        await self._session.flush()
        return await self._load_lecture(lecture.id)

    async def get_lecture(self, lecture_id: uuid.UUID) -> Lecture:
        lecture = await self._load_lecture(lecture_id)
        if lecture is None:
            raise LectureNotFoundError(str(lecture_id))
        return lecture

    async def list_lectures(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Lecture]:
        stmt = (
            select(Lecture)
            .options(
                selectinload(Lecture.subject),
                selectinload(Lecture.classroom),
                selectinload(Lecture.attendance_records),
            )
            .order_by(Lecture.scheduled_start.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            stmt = stmt.where(Lecture.status == status)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _get_mutable_lecture(self, lecture_id: uuid.UUID) -> Lecture:
        lecture = await self._load_lecture(lecture_id)
        if lecture is None:
            raise LectureNotFoundError(str(lecture_id))
        if lecture.status == LectureStatus.FINALIZED.value:
            raise LectureLifecycleError(
                "finalized lectures cannot be modified",
                status_code=409,
            )
        return lecture

    async def _load_lecture(self, lecture_id: uuid.UUID) -> Lecture | None:
        stmt = (
            select(Lecture)
            .where(Lecture.id == lecture_id)
            .options(
                selectinload(Lecture.subject),
                selectinload(Lecture.classroom),
                selectinload(Lecture.attendance_records),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _active_enrolled_student_ids(self, subject_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = select(Enrollment.student_id).where(
            Enrollment.subject_id == subject_id,
            Enrollment.active.is_(True),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _require_status(
        lecture: Lecture,
        allowed: set[LectureStatus],
        *,
        action: str,
    ) -> None:
        try:
            current = LectureStatus(lecture.status)
        except ValueError as exc:
            raise LectureLifecycleError(
                f"lecture has unknown status {lecture.status!r}",
                status_code=409,
            ) from exc

        if current not in allowed:
            allowed_values = ", ".join(sorted(s.value for s in allowed))
            raise LectureLifecycleError(
                f"cannot {action} lecture in status {current.value!r}; "
                f"expected one of: {allowed_values}",
                status_code=409,
            )
