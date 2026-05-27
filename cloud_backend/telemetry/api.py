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

log = logging.getLogger("cloud_backend.telemetry")

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
    log.info(
        "session_start session_id=%s experiment_label=%r device=%s",
        req.session_id, req.experiment_label, req.device_id,
    )
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
    log.info(
        "session_end session_id=%s ended_at=%s summary_keys=%s",
        req.session_id, req.ended_at,
        list((req.summary or {}).keys()),
    )
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

    log.debug(
        "ingest session_id=%s received=%d persisted=%d",
        batch.session_id, len(serialised), persisted,
    )

    return IngestAck(
        session_id=batch.session_id,
        received=len(serialised),
        persisted=persisted,
    )
