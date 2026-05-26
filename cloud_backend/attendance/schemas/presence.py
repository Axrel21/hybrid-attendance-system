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


class PresenceEventResult(BaseModel):
    """Response from POST /presence/events."""

    accepted: bool
    message: str = "presence event received"
    camera_id: str
    track_id: int
    event: str
    occupancy: int
