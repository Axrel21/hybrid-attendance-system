# cloud_backend/__init__.py
"""Extended cloud-side backend for the Hybrid Edge–Cloud platform.

Layered on top of the verification-only ``cloud/`` server. The composite
FastAPI app lives in :mod:`cloud_backend.server`; the submodules expose:

* :mod:`cloud_backend.storage`            — filesystem-backed session + event store
* :mod:`cloud_backend.schemas`            — Pydantic models for the new API surface
* :mod:`cloud_backend.telemetry.api`      — ingestion router (edge → cloud)
* :mod:`cloud_backend.dashboard.api`      — read-side dashboard router
* :mod:`cloud_backend.dashboard.websocket`— live-telemetry WebSocket
* :mod:`cloud_backend.experiments.registry` — session / experiment aggregation
* :mod:`cloud_backend.analytics.metrics`  — ROC / FAR / FRR / EER + summary helpers

Design rules:

* The verification flow (``cloud/main.py`` ``POST /verify/image``) is the
  authoritative offload path. ``cloud_backend`` augments it; it does not
  replace it.
* No database — sessions and events live on the filesystem (JSON +
  JSONL) under ``$CLOUD_STORAGE_DIR`` (default ``<repo>/cloud_storage/``).
  Filesystem state survives restarts; rotating it is just ``rm -rf``.
* The edge runtime never depends on ``cloud_backend``. The Pi
  continues to operate offline; telemetry upload is opt-in.
"""
