"""Dashboard-facing report route registration (D5 Track 4).

Re-exports attendance report router for composite server mount.
No frontend assets — existing dashboard polls /attendance/report/* directly.
"""

from cloud_backend.attendance.report_api import router as attendance_report_router

__all__ = ["attendance_report_router"]
