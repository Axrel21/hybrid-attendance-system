# cloud_backend/telemetry/api.py
"""Telemetry ingestion router.

Endpoints (all idempotent at the storage layer — re-posting a session
start overwrites the metadata file atomically):

* ``POST  /telemetry/sessions/start`` — register a new session.
* ``POST  /telemetry/sessions/end``   — mark a session ended; persist summary.
* ``POST  /telemetry/ingest``         — accept a batch of telemetry events.
* ``GET   /telemetry/healthz``        — lightweight check (storage reachable).

The router also broadcasts ingested events into the WebSocket hub
(:mod:`cloud_backend.dashboard.websocket`) for live dashboard consumers.
Broadcasting is best-effort: WS failures never affect ingest success.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from cloud_backend.dashboard import websocket as ws_hub
from cloud_backend.schemas import (
    IngestAck,
    SessionAck,
    SessionEndRequest,
    SessionStartRequest,
    TelemetryBatch,
)
from cloud_backend.storage import get_default_storage
from cloud_backend.system.observability import log_ops

log = logging.getLogger("cloud_backend.telemetry")


def _edge_runtime_snapshot(events: list[dict[str, Any]]) -> str | None:
    """Compact runtime line from the tail of an ingest batch."""
    node = "edge"
    temp = None
    fan = None
    offload_count = 0
    diag_count = 0

    for ev in reversed(events[-20:]):
        fields = ev.get("fields") if isinstance(ev.get("fields"), dict) else {}
        event_type = ev.get("event_type") or ""
        if event_type in ("frame_telemetry", "telemetry"):
            if temp is None:
                try:
                    value = float(fields.get("cpu_temp_c") or 0)
                    if value > 0:
                        temp = value
                except (TypeError, ValueError):
                    pass
            if fan is None and fields.get("fan_state"):
                fan = str(fields.get("fan_state")).strip()
        if event_type == "diagnostic":
            diag_count += 1
            if fields.get("decision") == "OFFLOAD_TO_CLOUD":
                offload_count += 1

    if temp is None and fan is None and diag_count == 0:
        return None

    offload_pct = round((offload_count / diag_count) * 100) if diag_count else None
    parts = [node]
    if temp is not None:
        parts.append(f"temp={temp:.0f}C")
    if fan:
        parts.append(f"fan={fan.upper()}")
    if offload_pct is not None:
        parts.append(f"offload={offload_pct}%")
    return " ".join(parts)


def _device_label(device_id: str | None, session_id: str) -> str:
    return device_id or session_id

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/healthz")
async def telemetry_health() -> Dict[str, Any]:
    storage = get_default_storage()
    return {
        "status": "ok",
        "storage_root": str(storage.root),
        "sessions_dir": str(storage.sessions_dir),
        "ts_ms": int(time.time() * 1000),
    }


@router.post("/sessions/start", response_model=SessionAck)
async def telemetry_session_start(req: SessionStartRequest) -> SessionAck:
    storage = get_default_storage()
    try:
        sdir = storage.record_session_start(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    device = _device_label(req.device_id, req.session_id)
    log_ops(log, "EDGE", f"Node online: {device} session={req.session_id}")
    return SessionAck(
        session_id=req.session_id,
        accepted=True,
        storage_path=str(sdir),
        detail="session metadata persisted",
    )


@router.post("/sessions/end", response_model=SessionAck)
async def telemetry_session_end(req: SessionEndRequest) -> SessionAck:
    storage = get_default_storage()
    try:
        path = storage.record_session_end(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    log_ops(log, "EDGE", f"Node offline: session={req.session_id}")
    return SessionAck(
        session_id=req.session_id,
        accepted=True,
        storage_path=str(path),
        detail="session summary persisted",
    )


@router.post("/ingest", response_model=IngestAck)
async def telemetry_ingest(batch: TelemetryBatch) -> IngestAck:
    if not batch.events:
        return IngestAck(
            session_id=batch.session_id, received=0, persisted=0,
            detail="empty batch",
        )

    storage = get_default_storage()
    serialised = [ev.model_dump() for ev in batch.events]

    try:
        persisted = storage.append_events(batch.session_id, serialised)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Best-effort fanout to live dashboards. Never let WS errors break ingest.
    try:
        await ws_hub.broadcast(batch.session_id, serialised)
    except Exception:  # noqa: BLE001
        log.exception("WS broadcast failed (ingest still succeeded)")

    snapshot = _edge_runtime_snapshot(serialised)
    if snapshot:
        detail = storage.get_session(batch.session_id) or {}
        meta = detail.get("metadata") or {}
        node = _device_label(meta.get("device_id"), batch.session_id)
        line = snapshot.replace("edge", node, 1)
        temp_value = None
        for ev in reversed(serialised[-20:]):
            fields = ev.get("fields") if isinstance(ev.get("fields"), dict) else {}
            try:
                temp_value = float(fields.get("cpu_temp_c") or 0)
            except (TypeError, ValueError):
                temp_value = None
            if temp_value and temp_value > 0:
                break
        if temp_value and temp_value >= 75:
            log_ops(log, "EDGE", f"{line} state=degraded", level=logging.WARNING)
        elif temp_value and temp_value >= 65:
            log_ops(log, "EDGE", f"{line} state=warning", level=logging.WARNING)
        else:
            log_ops(log, "EDGE", line)
    else:
        log.debug(
            "ingest session_id=%s received=%d persisted=%d",
            batch.session_id, len(serialised), persisted,
        )

    return IngestAck(
        session_id=batch.session_id,
        received=len(serialised),
        persisted=persisted,
    )
