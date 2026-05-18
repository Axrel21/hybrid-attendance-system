"""Pydantic schemas for recognition-event ingestion."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RecognitionEvent(BaseModel):
    """Inbound wire contract — posted by edge runtime.

    The server resolves the active lecture internally; the edge does NOT
    supply lecture_id so that this endpoint stays source-agnostic and
    does not require the edge to track session context.
    """

    gallery_identity: str = Field(..., min_length=1, max_length=200)
    confidence: float = Field(..., ge=0.0, le=1.0)
    timestamp_ms: Optional[int] = Field(
        default=None,
        description="Edge wall-clock milliseconds (advisory; never used for window decisions)",
    )
    source: str = Field(
        default="edge_runtime",
        max_length=50,
        description="Event origin tag, e.g. 'edge_runtime' or 'arcface_cloud'",
    )


class IngestionResult(BaseModel):
    """Response from POST /attendance/recognition/events."""

    accepted: bool
    disposition: str
    gallery_identity: str
    lecture_id: Optional[str] = None
    record_id: Optional[str] = None
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    detail: Optional[str] = None
