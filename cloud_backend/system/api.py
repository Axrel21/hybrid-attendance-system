"""System health and config API (D5 Track 5)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from cloud_backend.system.health_checks import (
    build_attendance_health,
    build_root_health,
    build_surveillance_health,
)
from cloud_backend.system.settings import get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_root() -> dict:
    """Composite service health."""
    return await build_root_health()


@router.get("/health/attendance")
async def health_attendance() -> dict:
    """Attendance pipeline health and in-memory counts."""
    return await build_attendance_health()


@router.get("/health/surveillance")
async def health_surveillance() -> dict:
    """Surveillance integration health (external runtime)."""
    return build_surveillance_health()


@router.get("/system/config")
async def system_config() -> dict:
    """Safe, non-secret configuration summary."""
    return get_settings().safe_summary()


def register_exception_handlers(app) -> None:
    """Map database outages to 503 without crashing the process."""

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable_handler(_request, exc: SQLAlchemyError):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "database unavailable",
                "error": type(exc).__name__,
            },
        )
