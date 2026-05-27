# cloud_backend/schemas.py
"""Pydantic models for the cloud_backend API surface.

These are intentionally permissive — fields default to ``None`` so older
edge clients posting partial payloads do not get rejected. The wire
field names are mirrored verbatim from :mod:`shared.schemas` so dashboards
and CLI tools can rely on a single source of truth.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Session lifecycle ─────────────────────────────────────────────────────────

class SessionStartRequest(BaseModel):
    """Edge announces a new experiment session has begun."""

    session_id: str
    started_at: str = Field(..., description="ISO-8601 timestamp (producer wall clock)")
    experiment_label: str = ""
    device_id: Optional[str] = None
    hostname: Optional[str] = None
    camera_backend: Optional[str] = None
    headless: Optional[bool] = None
    simulate_pi: Optional[bool] = None
    thresholds: Optional[Dict[str, Any]] = None
    hardware: Optional[Dict[str, Any]] = None
    environment: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    # Experiment-protocol sidecar (research/experiment_protocol.py). Optional;
    # older edge clients omit it. Forward-compatible.
    protocol: Optional[Dict[str, Any]] = None


class SessionEndRequest(BaseModel):
    """Edge marks a session as finished; cloud writes the summary."""

    session_id: str
    ended_at: str
    summary: Optional[Dict[str, Any]] = None


class SessionAck(BaseModel):
    """Generic ack for session lifecycle endpoints."""

    session_id: str
    accepted: bool
    storage_path: Optional[str] = None
    detail: Optional[str] = None


# ── Telemetry events ──────────────────────────────────────────────────────────

class TelemetryEvent(BaseModel):
    """One row of telemetry. ``fields`` carries the event-specific payload."""

    event_type: str
    timestamp_ms: int
    session_id: str
    experiment_label: str = ""
    frame_id: Optional[int] = None
    track_id: Optional[int] = None
    fields: Dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    """A batch of events sent by the edge uploader or a replay tool."""

    session_id: str
    events: List[TelemetryEvent]


class IngestAck(BaseModel):
    session_id: str
    received: int
    persisted: int
    rejected: int = 0
    detail: Optional[str] = None


# ── Dashboard read models ─────────────────────────────────────────────────────

class SessionSummaryRow(BaseModel):
    session_id: str
    experiment_label: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    event_count: int = 0
    has_summary: bool = False


class SessionListResponse(BaseModel):
    total: int
    sessions: List[SessionSummaryRow]


class SessionDetailResponse(BaseModel):
    session_id: str
    metadata: Dict[str, Any]
    summary: Optional[Dict[str, Any]] = None
    event_count: int = 0
    storage_path: str


class ExperimentRow(BaseModel):
    experiment_label: str
    session_count: int
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class ExperimentListResponse(BaseModel):
    total: int
    experiments: List[ExperimentRow]


class MetricResponse(BaseModel):
    metric: str
    session_id: Optional[str] = None
    experiment_label: Optional[str] = None
    sample_count: int
    value: Dict[str, Any]
    detail: Optional[str] = None
