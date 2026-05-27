"""Serve the minimal attendance operational dashboard (D.2B)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent / "static" / "attendance"

router = APIRouter(tags=["attendance-dashboard"])


@router.get("/dashboard/attendance")
async def attendance_dashboard_page() -> FileResponse:
    """Operational classroom-aware attendance monitor (polling UI)."""
    return FileResponse(_STATIC_DIR / "index.html")


def register_static_mount(app) -> None:
    """Mount CSS/JS assets for the attendance dashboard."""
    app.mount(
        "/dashboard/attendance/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="attendance_dashboard_static",
    )
