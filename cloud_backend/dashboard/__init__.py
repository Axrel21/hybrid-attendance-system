# cloud_backend/dashboard/__init__.py
"""Read-side dashboard APIs and live-telemetry WebSocket hub.

* :mod:`cloud_backend.dashboard.api`           — JSON read endpoints under ``/api/...``.
* :mod:`cloud_backend.dashboard.websocket`    — broadcast hub for ``/ws/telemetry``.
* :mod:`cloud_backend.dashboard.attendance_ui` — operational attendance monitor UI.
"""
from .api import router
from .attendance_ui import router as attendance_dashboard_router

__all__ = ["router", "attendance_dashboard_router"]
