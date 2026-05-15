# cloud_backend/telemetry/__init__.py
"""Telemetry ingestion subsystem.

The :mod:`cloud_backend.telemetry.api` module exposes the FastAPI router
that handles session lifecycle (start/end) and event-batch ingestion.
``cloud_backend.server`` registers the router on the composite app.
"""
from .api import router

__all__ = ["router"]
