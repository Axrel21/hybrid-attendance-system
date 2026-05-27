"""Derived lecture occupancy analytics from in-memory presence data."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cloud_backend.api.serializers import build_attendance_summary
from cloud_backend.attendance.evidence_queries import surveillance_cameras_by_classroom
from cloud_backend.attendance.presence_store import get_presence_store
from cloud_backend.attendance.presence_timeline import get_timeline_service
from cloud_backend.attendance.schemas.occupancy import (
    OccupancyAnalyticsResponse,
    OccupancyTimelinePoint,
)
from cloud_backend.models.lecture import Lecture

TIMELINE_BUCKET_MINUTES = 5
ARRIVAL_WINDOW_MINUTES = 10
RETENTION_END_WINDOW_MINUTES = 5


def _lecture_window_ms(lecture: Lecture) -> tuple[int, int]:
    start = lecture.actual_start or lecture.scheduled_start
    now = datetime.now(timezone.utc)
    if lecture.actual_end is not None:
        end = lecture.actual_end
    elif lecture.status in ("active_window_open", "active_window_closed"):
        end = now
    else:
        end = lecture.scheduled_end
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    if end_ms < start_ms:
        end_ms = start_ms
    return start_ms, end_ms


def _recognized_count(lecture: Lecture) -> int:
    summary = build_attendance_summary(lecture)
    return max(0, summary.total_enrolled - summary.undetected)


def _build_timeline(
    entries: list[dict],
    *,
    start_ms: int,
    bucket_ms: int,
) -> tuple[list[OccupancyTimelinePoint], int]:
    buckets: dict[int, int] = {}
    peak = 0

    for entry in entries:
        ts = int(entry.get("timestamp_ms", 0))
        if ts < start_ms:
            continue
        occ = int(entry.get("occupancy", 0))
        bucket_key = start_ms + ((ts - start_ms) // bucket_ms) * bucket_ms
        buckets[bucket_key] = max(buckets.get(bucket_key, 0), occ)
        peak = max(peak, occ)

    timeline = [
        OccupancyTimelinePoint(
            t=datetime.fromtimestamp(key / 1000, tz=timezone.utc).strftime("%H:%M"),
            occupancy=buckets[key],
        )
        for key in sorted(buckets)
    ]
    return timeline, peak


def _occupancy_near_end(
    entries: list[dict],
    *,
    end_ms: int,
    window_ms: int,
) -> int:
    window_start = max(0, end_ms - window_ms)
    samples = [
        int(entry.get("occupancy", 0))
        for entry in entries
        if window_start <= int(entry.get("timestamp_ms", 0)) <= end_ms
    ]
    if not samples:
        return 0
    return max(samples)


def _arrival_concentration(
    *,
    camera_ids: set[str],
    lecture_start_ms: int,
    window_ms: int,
) -> int:
    window_end = lecture_start_ms + window_ms
    timeline = get_timeline_service()
    sessions = timeline.list_sessions(include_inactive=True)
    count = 0
    for session in sessions:
        if session.track_id <= 0 or session.camera_id not in camera_ids:
            continue
        if lecture_start_ms <= session.first_seen <= window_end:
            count += 1
    return count


def compute_occupancy_analytics(
    *,
    lecture: Lecture,
    camera_ids: list[str],
) -> OccupancyAnalyticsResponse:
    start_ms, end_ms = _lecture_window_ms(lecture)
    camera_set = set(camera_ids)
    bucket_ms = TIMELINE_BUCKET_MINUTES * 60 * 1000
    retention_window_ms = RETENTION_END_WINDOW_MINUTES * 60 * 1000
    arrival_window_ms = ARRIVAL_WINDOW_MINUTES * 60 * 1000

    entries = [
        entry
        for entry in get_presence_store().all_entries()
        if entry.get("camera_id") in camera_set
        and start_ms <= int(entry.get("timestamp_ms", 0)) <= end_ms
    ]

    timeline, peak = _build_timeline(entries, start_ms=start_ms, bucket_ms=bucket_ms)
    near_end = _occupancy_near_end(
        entries,
        end_ms=end_ms,
        window_ms=retention_window_ms,
    )
    recognized = _recognized_count(lecture)

    consistency_ratio = round(peak / recognized, 3) if recognized > 0 else None
    retention_ratio = round(near_end / peak, 3) if peak > 0 else None
    arrivals = _arrival_concentration(
        camera_ids=camera_set,
        lecture_start_ms=start_ms,
        window_ms=arrival_window_ms,
    )

    return OccupancyAnalyticsResponse(
        lecture_id=str(lecture.id),
        peak_occupancy=peak,
        recognized_attendance_count=recognized,
        consistency_ratio=consistency_ratio,
        retention_ratio=retention_ratio,
        arrival_concentration=arrivals,
        arrival_window_minutes=ARRIVAL_WINDOW_MINUTES,
        retention_end_window_minutes=RETENTION_END_WINDOW_MINUTES,
        occupancy_near_end=near_end,
        timeline=timeline,
    )


async def fetch_lecture_occupancy_analytics(
    session: AsyncSession,
    lecture_id: uuid.UUID,
) -> OccupancyAnalyticsResponse | None:
    stmt = (
        select(Lecture)
        .where(Lecture.id == lecture_id)
        .options(selectinload(Lecture.attendance_records))
    )
    result = await session.execute(stmt)
    lecture = result.scalar_one_or_none()
    if lecture is None:
        return None

    surv_by_classroom = await surveillance_cameras_by_classroom(session)
    camera_ids = surv_by_classroom.get(lecture.classroom_id, [])
    presence_sessions = get_timeline_service().list_sessions(include_inactive=True)
    from cloud_backend.attendance.evidence_queries import surveillance_camera_ids_for_classroom

    camera_ids = surveillance_camera_ids_for_classroom(
        lecture.classroom_id,
        surv_by_classroom,
        presence_sessions,
    )

    return compute_occupancy_analytics(lecture=lecture, camera_ids=camera_ids)
