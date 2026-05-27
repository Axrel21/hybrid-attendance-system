"""Operational visibility routes — read-only introspection."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.api.serializers import (
    build_attendance_event_inspection,
    build_attendance_record_inspection,
    build_attendance_summary,
    build_lecture_response,
)
from cloud_backend.api.visibility_queries import (
    _infer_recognition_outcome,
    _parse_meta,
    count_recognition_logs,
    fetch_active_lecture,
    fetch_camera_sources,
    fetch_lecture_events,
    fetch_lecture_records,
    fetch_recognition_logs,
    recognition_logs_matched_engine,
)
from cloud_backend.classroom.resolver import fetch_all_active_lectures
from cloud_backend.attendance.schemas.visibility import (
    ActiveLectureByClassroomEntry,
    ActiveLectureSummaryResponse,
    ActiveLecturesByClassroomResponse,
    AttendanceEventListResponse,
    AttendanceRecordListResponse,
    CameraSourceListResponse,
    CameraSourceResponse,
    RecognitionLogEntryResponse,
    RecognitionLogListResponse,
)
from cloud_backend.db.session import get_async_session
from cloud_backend.sessions.exceptions import LectureLifecycleError

router = APIRouter(prefix="/attendance", tags=["attendance-visibility"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


@router.get("/lectures/active", response_model=ActiveLectureSummaryResponse)
async def get_active_lecture_summary(
    classroom_id: Optional[uuid.UUID] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ActiveLectureSummaryResponse:
    lecture = await fetch_active_lecture(session, classroom_id=classroom_id)
    if lecture is None:
        return ActiveLectureSummaryResponse(
            active=False,
            classroom_id=classroom_id,
            resolution_mode="classroom_scoped" if classroom_id else "global",
        )

    return ActiveLectureSummaryResponse(
        active=True,
        lecture=build_lecture_response(lecture),
        attendance_summary=build_attendance_summary(lecture),
        classroom_id=lecture.classroom_id,
        resolution_mode="classroom_scoped" if classroom_id else "global",
    )


@router.get("/classrooms/active", response_model=ActiveLecturesByClassroomResponse)
async def list_active_lectures_by_classroom(
    session: AsyncSession = Depends(get_db_session),
) -> ActiveLecturesByClassroomResponse:
    lectures = await fetch_all_active_lectures(session)
    entries = [
        ActiveLectureByClassroomEntry(
            classroom_id=lecture.classroom_id,
            classroom_name=lecture.classroom.name,
            lecture=build_lecture_response(lecture),
            attendance_summary=build_attendance_summary(lecture),
        )
        for lecture in lectures
    ]
    return ActiveLecturesByClassroomResponse(
        total=len(entries),
        active_lectures=entries,
    )


@router.get("/sources", response_model=CameraSourceListResponse)
async def list_camera_sources(
    classroom_id: Optional[uuid.UUID] = Query(default=None),
    active_only: bool = Query(default=True),
    session: AsyncSession = Depends(get_db_session),
) -> CameraSourceListResponse:
    rows = await fetch_camera_sources(
        session,
        classroom_id=classroom_id,
        active_only=active_only,
    )
    sources = [
        CameraSourceResponse(
            id=source.id,
            camera_id=source.camera_id,
            classroom_id=source.classroom_id,
            classroom_name=classroom.name,
            label=source.label,
            location=source.location,
            active=source.active,
        )
        for source, classroom in rows
    ]
    return CameraSourceListResponse(total=len(sources), sources=sources)


@router.get("/lectures/{lecture_id}/records", response_model=AttendanceRecordListResponse)
async def list_lecture_records(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceRecordListResponse:
    try:
        rows = await fetch_lecture_records(session, lecture_id)
    except LectureLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    records = [
        build_attendance_record_inspection(record, student)
        for record, student in rows
    ]
    return AttendanceRecordListResponse(
        lecture_id=lecture_id,
        total=len(records),
        records=records,
    )


@router.get("/lectures/{lecture_id}/events", response_model=AttendanceEventListResponse)
async def list_lecture_events(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AttendanceEventListResponse:
    try:
        rows = await fetch_lecture_events(session, lecture_id)
    except LectureLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    events = [
        build_attendance_event_inspection(event, record, student)
        for event, record, student in rows
    ]
    return AttendanceEventListResponse(
        lecture_id=lecture_id,
        total=len(events),
        events=events,
    )


@router.get("/recognition/logs", response_model=RecognitionLogListResponse)
async def list_recognition_logs(
    lecture_id: Optional[uuid.UUID] = Query(default=None),
    classroom_id: Optional[uuid.UUID] = Query(default=None),
    camera_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> RecognitionLogListResponse:
    entries = await fetch_recognition_logs(
        session,
        lecture_id=lecture_id,
        classroom_id=classroom_id,
        camera_id=camera_id,
        limit=limit,
        offset=offset,
    )
    total = await count_recognition_logs(
        session,
        lecture_id=lecture_id,
        classroom_id=classroom_id,
        camera_id=camera_id,
    )
    matched = await recognition_logs_matched_engine(session, entries)

    logs = []
    for entry in entries:
        meta = _parse_meta(entry.meta_json)
        accepted, outcome = _infer_recognition_outcome(
            lecture_id=entry.lecture_id,
            meta=meta,
            matched_engine_event=matched.get(entry.id, False),
        )
        logs.append(
            RecognitionLogEntryResponse(
                id=entry.id,
                lecture_id=entry.lecture_id,
                classroom_id=entry.classroom_id,
                camera_id=entry.camera_id,
                gallery_identity=entry.gallery_identity,
                confidence=entry.confidence,
                source=entry.source,
                timestamp_ms=entry.timestamp_ms,
                received_at=entry.received_at,
                accepted=accepted,
                outcome=outcome,
            )
        )

    return RecognitionLogListResponse(total=total, logs=logs)
