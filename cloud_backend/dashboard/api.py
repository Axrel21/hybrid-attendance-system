# cloud_backend/dashboard/api.py
"""Read-side dashboard router.

Endpoints (all read-only; no side effects on storage):

* ``GET /api/sessions``                            — paginated session list.
* ``GET /api/sessions/{session_id}``               — metadata + summary + event count.
* ``GET /api/sessions/{session_id}/telemetry``     — paginated event stream.
* ``GET /api/sessions/{session_id}/summary``       — summary only (lighter payload).
* ``GET /api/experiments``                         — sessions grouped by experiment_label.
* ``GET /api/experiments/{experiment_label}``      — sessions for one experiment label.
* ``GET /api/metrics/agreement``                   — edge/cloud agreement metric.
* ``GET /api/metrics/offload``                     — offload outcome distribution.
* ``GET /api/metrics/latency``                     — latency percentile summary.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from cloud_backend.analytics import metrics as metrics_mod
from cloud_backend.experiments.registry import ExperimentRegistry
from cloud_backend.schemas import (
    ExperimentListResponse,
    ExperimentRow,
    MetricResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionSummaryRow,
)
from cloud_backend.storage import get_default_storage

log = logging.getLogger("cloud_backend.dashboard")

router = APIRouter(prefix="/api", tags=["dashboard"])


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    experiment_label: Optional[str] = Query(default=None),
) -> SessionListResponse:
    storage = get_default_storage()
    records = storage.list_sessions()
    if experiment_label is not None:
        records = [r for r in records if r.experiment_label == experiment_label]

    total = len(records)
    page = records[offset : offset + limit]
    rows = [
        SessionSummaryRow(
            session_id=r.session_id,
            experiment_label=r.experiment_label,
            started_at=r.started_at,
            ended_at=r.ended_at,
            event_count=r.event_count,
            has_summary=r.has_summary,
        )
        for r in page
    ]
    return SessionListResponse(total=total, sessions=rows)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str) -> SessionDetailResponse:
    storage = get_default_storage()
    detail = storage.get_session(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id={session_id!r}")
    return SessionDetailResponse(**detail)


@router.get("/sessions/{session_id}/telemetry")
async def get_session_telemetry(
    session_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    storage = get_default_storage()
    if storage.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id={session_id!r}")
    events: List[Dict[str, Any]] = list(
        storage.iter_session_events(session_id, limit=limit, offset=offset)
    )
    total = storage.session_event_count(session_id)
    return {
        "session_id": session_id,
        "total": total,
        "offset": offset,
        "returned": len(events),
        "events": events,
    }


@router.get("/sessions/{session_id}/summary")
async def get_session_summary(session_id: str) -> Dict[str, Any]:
    storage = get_default_storage()
    detail = storage.get_session(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id={session_id!r}")
    return {
        "session_id": detail["session_id"],
        "metadata": detail["metadata"],
        "summary": detail["summary"],
        "event_count": detail["event_count"],
    }


# ── Experiments ───────────────────────────────────────────────────────────────

@router.get("/experiments", response_model=ExperimentListResponse)
async def list_experiments() -> ExperimentListResponse:
    registry = ExperimentRegistry(get_default_storage())
    rows = registry.list_experiments()
    return ExperimentListResponse(
        total=len(rows),
        experiments=[ExperimentRow(**r) for r in rows],
    )


@router.get("/experiments/{experiment_label}")
async def experiment_detail(experiment_label: str) -> Dict[str, Any]:
    registry = ExperimentRegistry(get_default_storage())
    summary = registry.experiment_summary(experiment_label)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment_label={experiment_label!r}")
    return summary


# ── Metrics ───────────────────────────────────────────────────────────────────

def _collect_events(session_id: Optional[str], experiment_label: Optional[str]) -> List[Dict[str, Any]]:
    """Pull events for the requested scope. session_id wins over label."""
    storage = get_default_storage()
    if session_id is not None:
        if storage.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown session_id={session_id!r}")
        return list(storage.iter_session_events(session_id))
    if experiment_label is not None:
        out: List[Dict[str, Any]] = []
        for rec in storage.list_sessions():
            if rec.experiment_label != experiment_label:
                continue
            out.extend(storage.iter_session_events(rec.session_id))
        return out
    # No scope -> all sessions (capped at 50k to avoid OOM).
    out_all: List[Dict[str, Any]] = []
    for rec in storage.list_sessions():
        out_all.extend(storage.iter_session_events(rec.session_id))
        if len(out_all) >= 50_000:
            break
    return out_all


@router.get("/metrics/agreement", response_model=MetricResponse)
async def metric_agreement(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = metrics_mod.agreement_rate(events)
    return MetricResponse(
        metric="edge_cloud_agreement",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
    )


@router.get("/metrics/offload", response_model=MetricResponse)
async def metric_offload(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = metrics_mod.offload_outcome_distribution(events)
    return MetricResponse(
        metric="offload_outcome",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
    )


@router.get("/metrics/latency", response_model=MetricResponse)
async def metric_latency(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
    key: str = Query(default="cloud_rtt_ms"),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = metrics_mod.latency_summary(events, key)
    return MetricResponse(
        metric=f"latency:{key}",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
        detail=f"percentiles over event_field '{key}'",
    )
