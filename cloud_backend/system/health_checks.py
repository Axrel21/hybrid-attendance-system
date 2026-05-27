"""Health probe helpers (D5 Track 5)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from cloud_backend.attendance.evidence_store import get_evidence_store
from cloud_backend.attendance.finalization_store import get_finalization_store
from cloud_backend.attendance.presence_store import get_presence_store
from cloud_backend.attendance.presence_timeline import get_timeline_service
from cloud_backend.db.session import get_session_factory
from cloud_backend.system.settings import get_settings

log = logging.getLogger("cloud_backend.system.health")


def verification_status() -> dict[str, Any]:
    """Read-only snapshot of cloud/main.py verification stack (no logic changes)."""
    try:
        import main as cloud_main  # type: ignore[import-not-found]

        gallery = getattr(cloud_main, "gallery", None)
        verifier = getattr(cloud_main, "verifier", None)
        return {
            "status": "ok" if verifier is not None else "degraded",
            "gallery_size": len(gallery) if gallery else 0,
            "model_loaded": verifier is not None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "detail": type(exc).__name__}


async def check_database() -> dict[str, Any]:
    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        return {"ok": True, "detail": "connected"}
    except Exception as exc:  # noqa: BLE001
        from cloud_backend.system.observability import log_ops

        log_ops(log, "WARN", f"Database health check failed: {exc}", level=logging.WARNING)
        return {"ok": False, "detail": str(exc)[:200]}


def attendance_counts() -> dict[str, int]:
    presence_events = len(get_presence_store().recent(10_000))
    presence_sessions = len(get_timeline_service().list_sessions())
    frozen_lectures = get_finalization_store().lecture_count()
    return {
        "presence_events": presence_events,
        "presence_sessions": presence_sessions,
        "frozen_lectures": frozen_lectures,
        "evidence_records": get_evidence_store().record_count(),
    }


async def build_attendance_health() -> dict[str, Any]:
    db = await check_database()
    counts = attendance_counts()
    ready = db["ok"]
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "database": db,
        "counts": counts,
        "pipelines": {
            "recognition": "ingest_only",
            "presence": "ok",
            "evidence": "ok",
            "eligibility": "ok",
            "decision": "ok",
            "finalization": "ok",
            "report": "ok",
        },
    }


def build_surveillance_health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "external",
        "ready": True,
        "runtime": "laptop_process",
        "detail": "Surveillance runs via python -m surveillance.run on a separate host",
        "cloud_presence_endpoint": f"{settings.attendance.cloud_server_url}/presence/events",
        "presence_sessions_visible_to_cloud": len(get_timeline_service().list_sessions()),
        "surveillance_presence_enabled": settings.attendance.surveillance_presence_enabled,
    }


async def build_root_health() -> dict[str, Any]:
    attendance = await build_attendance_health()
    surveillance = build_surveillance_health()
    verification = verification_status()
    overall_ready = attendance.get("ready", False)
    status = "ok" if overall_ready else "degraded"
    return {
        "status": status,
        "ready": overall_ready,
        "profile": get_settings().profile,
        "verification": verification,
        "attendance": attendance,
        "surveillance": surveillance,
    }
