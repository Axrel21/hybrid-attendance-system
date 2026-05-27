"""Read-only lecture queries for eligibility (D4 Track 2)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.models.lecture import Lecture


async def fetch_lecture(
    session: AsyncSession,
    lecture_id: uuid.UUID,
) -> Lecture | None:
    return await session.get(Lecture, lecture_id)


def lecture_duration_sec(lecture: Lecture) -> int:
    """Scheduled or actual lecture span in seconds."""
    if lecture.actual_start is not None and lecture.actual_end is not None:
        delta = lecture.actual_end - lecture.actual_start
        return max(0, int(delta.total_seconds()))

    delta = lecture.scheduled_end - lecture.scheduled_start
    return max(0, int(delta.total_seconds()))
