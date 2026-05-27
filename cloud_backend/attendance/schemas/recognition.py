"""Pydantic schemas for recognition-event ingestion."""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class RecognitionEvent(BaseModel):
    """Inbound wire contract — posted by edge runtime.

    Lecture resolution is classroom-scoped when ``classroom_id`` or
    ``camera_id`` is supplied (D.2A).  Omit both for D.1 global fallback.
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
    classroom_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Target classroom for lecture resolution (D.2A)",
    )
    camera_id: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Registered camera id; resolves to classroom via source registry",
    )


class IngestionResult(BaseModel):
    """Response from POST /attendance/recognition/events."""

    accepted: bool
    disposition: str
    gallery_identity: str
    lecture_id: Optional[str] = None
    classroom_id: Optional[str] = None
    camera_id: Optional[str] = None
    record_id: Optional[str] = None
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    detail: Optional[str] = None
