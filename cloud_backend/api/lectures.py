"""Lecture CRUD and lifecycle routes."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.api.serializers import build_lecture_response
from cloud_backend.db.session import get_async_session
from cloud_backend.attendance.schemas.lecture import LectureCreate, LectureListResponse, LectureResponse
from cloud_backend.sessions.controller import LectureSessionController
from cloud_backend.sessions.exceptions import LectureLifecycleError
from cloud_backend.system.observability import log_ops

router = APIRouter(prefix="/attendance", tags=["attendance"])
log = logging.getLogger(__name__)


def _lecture_label(response: LectureResponse) -> str:
    return f"{response.subject_code} {response.classroom_name}"


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


@router.post("/lectures", response_model=LectureResponse, status_code=201)
async def create_lecture(
    payload: LectureCreate,
    session: AsyncSession = Depends(get_db_session),
) -> LectureResponse:
    controller = LectureSessionController(session)
    try:
        lecture = await controller.create_lecture(
            subject_id=payload.subject_id,
            classroom_id=payload.classroom_id,
            scheduled_start=payload.scheduled_start,
            scheduled_end=payload.scheduled_end,
            attendance_window_minutes=payload.attendance_window_minutes,
        )
        await session.commit()
        response = build_lecture_response(lecture)
        log_ops(log, "ATTENDANCE", f"Lecture created: {_lecture_label(response)}")
        return response
    except LectureLifecycleError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/lectures", response_model=LectureListResponse)
async def list_lectures(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> LectureListResponse:
    controller = LectureSessionController(session)
    lectures = await controller.list_lectures(status=status, limit=limit, offset=offset)
    responses = [build_lecture_response(lecture) for lecture in lectures]
    return LectureListResponse(total=len(responses), lectures=responses)


@router.get("/lectures/{lecture_id}", response_model=LectureResponse)
async def get_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> LectureResponse:
    controller = LectureSessionController(session)
    try:
        lecture = await controller.get_lecture(lecture_id)
        return build_lecture_response(lecture)
    except LectureLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.patch("/lectures/{lecture_id}/start", response_model=LectureResponse)
async def start_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> LectureResponse:
    controller = LectureSessionController(session)
    try:
        lecture = await controller.start_lecture(lecture_id)
        await session.commit()
        response = build_lecture_response(lecture)
        log_ops(log, "ATTENDANCE", f"Lecture started: {_lecture_label(response)}")
        return response
    except LectureLifecycleError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.patch("/lectures/{lecture_id}/close", response_model=LectureResponse)
async def close_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> LectureResponse:
    controller = LectureSessionController(session)
    try:
        lecture = await controller.close_lecture(lecture_id)
        await session.commit()
        response = build_lecture_response(lecture)
        log_ops(log, "ATTENDANCE", f"Lecture closed: {_lecture_label(response)}")
        return response
    except LectureLifecycleError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.patch("/lectures/{lecture_id}/finalize", response_model=LectureResponse)
async def finalize_lecture(
    lecture_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> LectureResponse:
    controller = LectureSessionController(session)
    try:
        lecture = await controller.finalize_lecture(lecture_id)
        await session.commit()
        response = build_lecture_response(lecture)
        log_ops(log, "ATTENDANCE", f"Lecture finalized: {_lecture_label(response)}")
        return response
    except LectureLifecycleError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
