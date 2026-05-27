"""Schemas for lecture occupancy analytics (derived from presence data)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OccupancyTimelinePoint(BaseModel):
    t: str = Field(..., description="Bucket label HH:MM")
    occupancy: int = Field(..., ge=0)


class OccupancyAnalyticsResponse(BaseModel):
    lecture_id: str
    peak_occupancy: int = Field(..., ge=0)
    recognized_attendance_count: int = Field(..., ge=0)
    consistency_ratio: float | None = Field(
        None,
        description="peak_occupancy / recognized_attendance_count",
    )
    retention_ratio: float | None = Field(
        None,
        description="occupancy_near_end / peak_occupancy",
    )
    arrival_concentration: int = Field(
        ...,
        ge=0,
        description="Tracks appearing within the first arrival window minutes",
    )
    arrival_window_minutes: int = Field(..., ge=1)
    retention_end_window_minutes: int = Field(..., ge=1)
    occupancy_near_end: int = Field(..., ge=0)
    timeline: list[OccupancyTimelinePoint] = Field(default_factory=list)
