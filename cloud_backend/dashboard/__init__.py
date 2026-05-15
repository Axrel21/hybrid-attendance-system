# cloud_backend/dashboard/__init__.py
"""Read-side dashboard APIs and live-telemetry WebSocket hub.

* :mod:`cloud_backend.dashboard.api`       — JSON read endpoints under ``/api/...``.
* :mod:`cloud_backend.dashboard.websocket` — broadcast hub for ``/ws/telemetry``.
"""
from .api import router

__all__ = ["router"]
