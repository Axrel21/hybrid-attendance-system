# shared/schemas.py
"""Lazy accessors for per-run telemetry CSV column lists.

The authoritative column lists live next to the rotation logic that
consumes them:

* ``edge.main.DIAG_COLUMNS`` (``diagnostic_log.csv``)
* ``edge.telemetry.TELEMETRY_CSV_COLUMNS`` (``telemetry_log.csv``)

Both modules pull in cv2 / tflite at import time, so we cannot eagerly
import them from a cross-cutting package without forcing every consumer
to install the full edge stack. The accessors below import lazily, so:

* dashboard / aggregation code on the cloud host can call them when it
  actually needs the schema and surface a clean ImportError if the edge
  stack is missing;
* lightweight tools (manifest validators, packaging scripts) can import
  ``shared`` without paying that cost.

Adding columns is forward-compatible (CSV rotation handles old headers).
Reordering or renaming a column is a schema break.
"""
from __future__ import annotations

from typing import Tuple


def get_diag_columns() -> Tuple[str, ...]:
    """Return the canonical ``diagnostic_log.csv`` column list.

    Raises:
        ImportError: if ``edge.main`` cannot be imported in the current
            environment (typically because the edge runtime
            dependencies — cv2, tflite — are not installed on this
            host).
    """
    from edge.main import DIAG_COLUMNS  # noqa: WPS433
    return tuple(DIAG_COLUMNS)


def get_telemetry_csv_columns() -> Tuple[str, ...]:
    """Return the canonical ``telemetry_log.csv`` column list."""
    from edge.telemetry import TELEMETRY_CSV_COLUMNS  # noqa: WPS433
    return tuple(TELEMETRY_CSV_COLUMNS)


# Stable attendance log header (small enough to mirror verbatim — keep in
# sync with the writer in ``edge.main.FinalHybridEdge.__init__``).
ATTENDANCE_CSV_COLUMNS: Tuple[str, ...] = (
    "name",
    "confidence",
    "timestamp",
    "latency",
    "liveness_label",
    "reason",
    "distance",
    "brightness",
    "motion_score",
    "geometry_score",
    "mode",
    "track_id",
)


# Stable experiment-session index row shape (written by
# ``config.experiment_session._append_session_index``). Documents the
# JSONL fields a dashboard can rely on.
EXPERIMENT_INDEX_FIELDS: Tuple[str, ...] = (
    "experiment_id",
    "started_at",
    "root",
    "telemetry_csv",
    "diagnostic_csv",
    "attendance_csv",
    "experiment_label",
)


__all__ = [
    "get_diag_columns",
    "get_telemetry_csv_columns",
    "ATTENDANCE_CSV_COLUMNS",
    "EXPERIMENT_INDEX_FIELDS",
]
