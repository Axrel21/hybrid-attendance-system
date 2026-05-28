"""Pydantic schemas for surveillance presence events (D3 Track 4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PresenceEventType = Literal["heartbeat", "appeared", "disappeared"]
AppearanceTrigger = Literal["entry", "lost", "recovery"]


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
    appearance_embedding: list[float] | None = Field(
        None,
        description="Experimental trigger-based OSNet embedding — never continuous",
    )
    appearance_trigger: AppearanceTrigger | None = Field(
        None,
        description="Why an embedding was extracted: entry | lost | recovery",
    )
    track_centroid_x: float | None = Field(None, ge=0.0, le=1.0)
    track_centroid_y: float | None = Field(None, ge=0.0, le=1.0)
    track_bbox: list[float] | None = Field(
        None,
        min_length=4,
        max_length=4,
        description="Normalized bbox x1,y1,x2,y2 for experimental recovery scoring",
    )
    track_duration_sec: int | None = Field(None, ge=0)

    @field_validator("appearance_embedding")
    @classmethod
    def _validate_embedding(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return None
        if not value or len(value) > 2048:
            raise ValueError("appearance_embedding must be non-empty and <= 2048 dims")
        return value


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
    continuity_label: str | None = Field(
        None,
        description="Experimental appearance continuity hint — not guaranteed identity",
    )
    continuity_note: str | None = None
    continuity_similarity: float | None = Field(None, ge=0.0, le=1.0)
    continuity_score: float | None = Field(None, ge=0.0, le=1.0)
    continuity_confidence: str | None = None
    continuity_recovered_from_track: int | None = Field(None, ge=0)
    continuity_recovery_age_ms: int | None = Field(None, ge=0)


class PresenceSessionListResponse(BaseModel):
    """Response from GET /presence/sessions."""

    total: int = Field(..., ge=0)
    sessions: list[PresenceSessionResponse]
