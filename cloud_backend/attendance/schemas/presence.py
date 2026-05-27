"""Pydantic schemas for surveillance presence events (D3 Track 4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PresenceEventType = Literal["heartbeat", "appeared", "disappeared"]


class PresenceEvent(BaseModel):
    """Inbound wire contract — posted by surveillance runtime only."""

    model_config = ConfigDict(extra="forbid")

    camera_id: str = Field(..., min_length=1, max_length=100)
    track_id: int = Field(..., ge=0, description="Anonymous local track id; 0 for heartbeat")
    event: PresenceEventType
    timestamp_ms: int = Field(..., ge=0)
    occupancy: int = Field(..., ge=0)
    in_entry_zone: bool | None = Field(
        None,
        description="Experimental: track centroid inside doorway ROI at event time",
    )


class PresenceEventResult(BaseModel):
    """Response from POST /presence/events."""

    accepted: bool
    message: str = "presence event received"
    camera_id: str
    track_id: int
    event: str
    occupancy: int


PresenceSessionStatus = Literal["active", "inactive"]


class PresenceSessionResponse(BaseModel):
    """Anonymous track session derived from presence events."""

    camera_id: str
    track_id: int
    first_seen: int = Field(..., description="First seen wall-clock ms")
    last_seen: int = Field(..., description="Last seen wall-clock ms")
    duration_sec: int = Field(..., ge=0)
    status: PresenceSessionStatus
    handoff_identity: str | None = Field(
        None,
        description="Experimental doorway entry correlation — not persistent identity",
    )
    handoff_confidence: str | None = None


class PresenceSessionListResponse(BaseModel):
    """Response from GET /presence/sessions."""

    total: int = Field(..., ge=0)
    sessions: list[PresenceSessionResponse]
