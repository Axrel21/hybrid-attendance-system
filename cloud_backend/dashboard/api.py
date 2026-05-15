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

from cloud_backend.analytics import (
    calibration as calibration_mod,
    evaluation as evaluation_mod,
    metrics as metrics_mod,
    quality as quality_mod,
    stabilization as stabilization_mod,
)
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


# ── Stabilization / experimentation (pass 5 additions) ────────────────────────

@router.get("/metrics/stabilization", response_model=MetricResponse)
async def metric_stabilization(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = stabilization_mod.stabilization_summary(events)
    return MetricResponse(
        metric="stabilization_summary",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n_events"],
        value=result,
        detail="orientation + confidence + PAD + thermal + bbox bundle",
    )


@router.get("/metrics/orientation", response_model=MetricResponse)
async def metric_orientation(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = stabilization_mod.orientation_stability(events)
    return MetricResponse(
        metric="orientation_stability",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n_tracks"],
        value=result,
    )


@router.get("/metrics/pad", response_model=MetricResponse)
async def metric_pad(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = stabilization_mod.pad_temporal(events)
    return MetricResponse(
        metric="pad_temporal",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
    )


@router.get("/metrics/thermal", response_model=MetricResponse)
async def metric_thermal(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
    threshold_c: float = Query(default=75.0),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = stabilization_mod.thermal_stats(events, threshold_c=threshold_c)
    return MetricResponse(
        metric="thermal",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
        detail=f"threshold_c={threshold_c}",
    )


@router.get("/metrics/threshold_sweep", response_model=MetricResponse)
async def metric_threshold_sweep(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
    th_high_min: float = Query(default=0.50),
    th_high_max: float = Query(default=0.95),
    steps: int = Query(default=19, ge=2, le=200),
    mid_offset: float = Query(default=0.15),
) -> MetricResponse:
    import numpy as np
    events = _collect_events(session_id, experiment_label)
    th_values = np.linspace(th_high_min, th_high_max, steps)
    result = calibration_mod.threshold_sweep(
        events, th_values.tolist(), mid_offset=mid_offset,
    )
    return MetricResponse(
        metric="threshold_sweep",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n_events"],
        value=result,
        detail=f"th_high in [{th_high_min}, {th_high_max}] x {steps} steps",
    )


@router.get("/metrics/confidence_distribution", response_model=MetricResponse)
async def metric_confidence_distribution(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
    key: str = Query(default="sim"),
    bins: int = Query(default=20, ge=2, le=200),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = calibration_mod.confidence_distribution(events, key=key, bins=bins)
    return MetricResponse(
        metric=f"confidence_distribution:{key}",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
    )


@router.get("/sessions/{session_id}/protocol")
async def session_protocol(session_id: str) -> Dict[str, Any]:
    registry = ExperimentRegistry(get_default_storage())
    protocol = registry.session_protocol(session_id)
    if protocol is None:
        # Distinguish "session unknown" from "session present but no protocol".
        if get_default_storage().get_session(session_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown session_id={session_id!r}")
        return {"session_id": session_id, "protocol": None,
                "detail": "session has no experiment_protocol.json sidecar"}
    return {"session_id": session_id, "protocol": protocol}


@router.get("/sessions/{session_id}/category")
async def session_category(session_id: str) -> Dict[str, Any]:
    registry = ExperimentRegistry(get_default_storage())
    cat = registry.session_category(session_id)
    if cat is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id={session_id!r}")
    return cat


# ── Quality tags (pass 6) ─────────────────────────────────────────────────────

@router.get("/metrics/quality_tags", response_model=MetricResponse)
async def metric_quality_tags(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = quality_mod.evaluate(events)
    return MetricResponse(
        metric="quality_tags",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n_events"],
        value=result,
        detail=f"{result['tag_count']} tags raised",
    )


@router.get("/sessions/{session_id}/quality_tags")
async def session_quality_tags(session_id: str) -> Dict[str, Any]:
    storage = get_default_storage()
    if storage.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id={session_id!r}")
    events = list(storage.iter_session_events(session_id))
    result = quality_mod.evaluate(events)
    return {"session_id": session_id, **result}


# ── Aggregation + comparison (pass 7) ─────────────────────────────────────────

def _per_session_metric_row(session_id: str) -> Dict[str, Any]:
    storage = get_default_storage()
    detail = storage.get_session(session_id)
    if detail is None:
        return {"session_id": session_id, "error": "unknown session"}
    events = list(storage.iter_session_events(session_id))
    stab = stabilization_mod.stabilization_summary(events)
    qual = quality_mod.evaluate(events)
    registry = ExperimentRegistry(storage)
    return {
        "session_id": session_id,
        "experiment_label": (detail.get("metadata") or {}).get("experiment_label"),
        "category": registry.session_category(session_id),
        "event_count": detail.get("event_count", 0),
        "stabilization": stab,
        "quality_tags": qual,
    }


@router.get("/aggregate/sessions")
async def aggregate_sessions_route(
    ids: List[str] = Query(..., description="repeat ?ids=... per session"),
) -> Dict[str, Any]:
    if not ids:
        raise HTTPException(status_code=422, detail="at least one session id required")
    rows = [_per_session_metric_row(sid) for sid in ids]
    return {"session_count": len(rows), "rows": rows}


@router.get("/compare/sessions")
async def compare_sessions_route(
    baseline: str = Query(..., description="baseline session id"),
    modified: str = Query(..., description="modified session id"),
) -> Dict[str, Any]:
    storage = get_default_storage()
    for sid in (baseline, modified):
        if storage.get_session(sid) is None:
            raise HTTPException(status_code=404, detail=f"unknown session_id={sid!r}")
    a = _per_session_metric_row(baseline)
    b = _per_session_metric_row(modified)
    diff_keys = [
        ("event_count", "event_count"),
        ("offload_trigger_rate", "stabilization.offload.outcome.offload_rate"),
        ("orientation_mode_flip_rate_mean", "stabilization.orientation.mode_flip_rate_mean"),
        ("thermal_p95", "stabilization.thermal.p95"),
        ("tag_count", "quality_tags.tag_count"),
    ]

    def _walk(d: Dict[str, Any], path: str) -> Any:
        cur: Any = d
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
            if cur is None:
                return None
        return cur

    rows: List[Dict[str, Any]] = []
    for label, path in diff_keys:
        va = _walk(a, path)
        vb = _walk(b, path)
        delta = (vb - va) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
        rows.append({"metric": label, "value_a": va, "value_b": vb, "delta": delta})
    return {"session_a": baseline, "session_b": modified, "rows": rows}


@router.get("/experiments/{experiment_label}/aggregate")
async def experiment_aggregate_route(experiment_label: str) -> Dict[str, Any]:
    storage = get_default_storage()
    recs = [r for r in storage.list_sessions() if r.experiment_label == experiment_label]
    if not recs:
        raise HTTPException(status_code=404, detail=f"no sessions for experiment_label={experiment_label!r}")
    rows = [_per_session_metric_row(r.session_id) for r in recs]
    return {"experiment_label": experiment_label, "session_count": len(rows), "rows": rows}


# ── Evaluation wrappers (pass 7) ──────────────────────────────────────────────

@router.get("/evaluation/pad", response_model=MetricResponse)
async def evaluation_pad(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
    attack_type: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = evaluation_mod.pad_confusion_matrix(events, attack_type=attack_type)
    return MetricResponse(
        metric="pad_confusion_matrix",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
    )


@router.get("/evaluation/orientation", response_model=MetricResponse)
async def evaluation_orientation(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = evaluation_mod.orientation_robustness(events)
    return MetricResponse(
        metric="orientation_robustness",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n_modes"],
        value=result,
    )


@router.get("/evaluation/thermal", response_model=MetricResponse)
async def evaluation_thermal(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
    threshold_c: float = Query(default=75.0),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = evaluation_mod.thermal_performance_tradeoff(events, threshold_c=threshold_c)
    return MetricResponse(
        metric="thermal_performance_tradeoff",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n"],
        value=result,
    )


@router.get("/evaluation/offload_efficiency", response_model=MetricResponse)
async def evaluation_offload_efficiency(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = evaluation_mod.offload_efficiency(events)
    return MetricResponse(
        metric="offload_efficiency",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=result["n_offloads"],
        value=result,
    )


@router.get("/evaluation/latency", response_model=MetricResponse)
async def evaluation_latency(
    session_id: Optional[str] = Query(default=None),
    experiment_label: Optional[str] = Query(default=None),
) -> MetricResponse:
    events = _collect_events(session_id, experiment_label)
    result = evaluation_mod.latency_distribution_comparison(events)
    rows = result.get("rows") or []
    sample = sum((r.get("n") or 0) for r in rows)
    return MetricResponse(
        metric="latency_distribution_comparison",
        session_id=session_id,
        experiment_label=experiment_label,
        sample_count=sample,
        value=result,
    )
