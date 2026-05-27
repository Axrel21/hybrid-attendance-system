"""Recognition event ingestion route."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.ingestor import RecognitionIngestor
from cloud_backend.attendance.schemas.recognition import IngestionResult, RecognitionEvent
from cloud_backend.db.session import get_async_session

router = APIRouter(prefix="/attendance", tags=["attendance"])


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


@router.post("/recognition/events", response_model=IngestionResult, status_code=200)
async def ingest_recognition_event(
    payload: RecognitionEvent,
    session: AsyncSession = Depends(get_db_session),
) -> IngestionResult:
    """Accept a recognition event from the edge runtime.

    Always returns 200. The ``accepted`` field and ``disposition`` tag in the
    response body describe what happened to the event. HTTP 4xx/5xx is reserved
    for malformed payloads and unhandled server errors only.
    """
    ingestor = RecognitionIngestor(session)
    try:
        result = await ingestor.ingest(
            gallery_identity=payload.gallery_identity,
            confidence=payload.confidence,
            source=payload.source,
            timestamp_ms=payload.timestamp_ms,
            classroom_id=payload.classroom_id,
            camera_id=payload.camera_id,
        )
        await session.commit()
        return IngestionResult(
            accepted=result.accepted,
            disposition=result.disposition,
            gallery_identity=result.gallery_identity,
            lecture_id=str(result.lecture_id) if result.lecture_id else None,
            classroom_id=str(result.classroom_id) if result.classroom_id else None,
            camera_id=result.camera_id,
            record_id=str(result.record_id) if result.record_id else None,
            from_state=result.from_state,
            to_state=result.to_state,
            detail=result.detail,
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
