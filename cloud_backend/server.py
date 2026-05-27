# cloud_backend/server.py
"""Composite FastAPI backend — verification + telemetry + dashboard + WS.

Architecture
------------
The verification flow lives in ``cloud/main.py`` and is owned by the
existing ``cloud/`` deployment. This module imports that app object,
mutates it to include the additional routers (``cloud_backend.telemetry``
and ``cloud_backend.dashboard``), and exposes the result as ``app`` for
uvicorn.

Run the composite from the repository root::

    bash deployment/cloud/run_backend.sh --host 0.0.0.0 --port 8000

The launcher script sets ``cwd=cloud/`` (so ``gallery/`` resolves next to
``cloud/main.py``) while adding the repo root to ``--app-dir`` so
``cloud_backend.server`` is importable.

Defensive imports
-----------------
The verification app pulls in ``cv2``, ``fastapi``, ``pydantic``,
``arcface_verifier`` (which loads InsightFace at lifespan time). If any
of those imports fail (typically because someone is doing
dashboard-only development on a host without the full cloud stack), we
fall back to a bare ``FastAPI()`` instance and proceed with the
telemetry + dashboard routers only — with a loud warning. In that mode,
``POST /verify/image`` returns 404, and that's the correct signal.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI

log = logging.getLogger("cloud_backend.server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLOUD_DIR = _REPO_ROOT / "cloud"


def _load_verification_app() -> "FastAPI":
    """Try to import the verification app from ``cloud/main.py``.

    Falls back to a placeholder FastAPI() if any heavy dependency is
    missing — see module docstring.
    """
    if str(_CLOUD_DIR) not in sys.path:
        sys.path.insert(0, str(_CLOUD_DIR))
    try:
        # ``cloud`` is a directory of bare-imported modules, not a package.
        from main import app as verification_app  # type: ignore[attr-defined]
        log.info("Composite app: verification routes mounted from cloud/main.py")
        return verification_app
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Verification app unavailable (%s: %s). "
            "Composite will serve telemetry + dashboard only; /verify/image will be absent.",
            type(exc).__name__, exc,
        )
        return FastAPI(
            title="Hybrid backend (verification disabled)",
            description="Cloud backend without the verification stack — "
                        "dashboard / telemetry only. Install cloud/requirements.txt "
                        "to enable /verify/image.",
        )


app: "FastAPI" = _load_verification_app()

# Late imports: these need fastapi but no cv2 / insightface.
from cloud_backend.telemetry.api import router as telemetry_router  # noqa: E402
from cloud_backend.dashboard.api import router as dashboard_router  # noqa: E402
from cloud_backend.api.visibility import router as attendance_visibility_router  # noqa: E402
from cloud_backend.api.lectures import router as attendance_lectures_router  # noqa: E402
from cloud_backend.api.recognition import router as recognition_router  # noqa: E402
from cloud_backend.dashboard import websocket as ws_module  # noqa: E402
from cloud_backend.dashboard.attendance_ui import (  # noqa: E402
    register_static_mount,
    router as attendance_dashboard_router,
)
from cloud_backend.storage import get_default_storage  # noqa: E402

app.include_router(telemetry_router)
app.include_router(dashboard_router)
app.include_router(attendance_dashboard_router)
register_static_mount(app)
app.include_router(attendance_visibility_router)
app.include_router(attendance_lectures_router)
app.include_router(recognition_router)
ws_module.register(app)


@app.get("/backend/info")
def backend_info() -> dict:
    """Diagnostic endpoint: confirms the composite app is active."""
    storage = get_default_storage()
    return {
        "app": "cloud_backend.server",
        "verification_routes_present": _has_route(app, "/verify/image"),
        "telemetry_routes_present": _has_route(app, "/telemetry/ingest"),
        "dashboard_routes_present": _has_route(app, "/api/sessions"),
        "attendance_routes_present": _has_route(app, "/attendance/lectures"),
        "recognition_route_present": _has_route(app, "/attendance/recognition/events"),
        "visibility_routes_present": _has_route(app, "/attendance/lectures/active"),
        "attendance_dashboard_present": _has_route(app, "/dashboard/attendance"),
        "ws_subscribers": ws_module.subscriber_count(),
        "storage_root": str(storage.root),
        "storage_dir_override": os.environ.get("CLOUD_STORAGE_DIR"),
    }


def _has_route(application: "FastAPI", path: str) -> bool:
    for route in application.router.routes:
        if getattr(route, "path", None) == path:
            return True
    return False


log.info(
    "cloud_backend.server ready: telemetry=%d dashboard=%d visibility=%d attendance=%d recognition=%d",
    sum(1 for _ in telemetry_router.routes),
    sum(1 for _ in dashboard_router.routes),
    sum(1 for _ in attendance_visibility_router.routes),
    sum(1 for _ in attendance_lectures_router.routes),
    sum(1 for _ in recognition_router.routes),
)
