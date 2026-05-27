"""Composite backend logging — pipeline events visible, dashboard polling quiet."""

from __future__ import annotations

import logging
import os
import re
from typing import Final

# Read-path side effects triggered by dashboard polling (5s interval).
_READ_PATH_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "evidence_generated",
        "report_generated",
        "eligibility_generated",
        "decision_generated",
        "finalization_generated",
    }
)

# Uvicorn / middleware access lines for dashboard/health polling routes.
_POLLING_ACCESS_RE = re.compile(
    r'"(?:GET|HEAD|OPTIONS)\s+(?:'
    r"/presence/sessions(?:\?|\s|\"|$)|"
    r"/attendance/evidence(?:/|\?|\s|\"|$)|"
    r"/attendance/classrooms/active(?:\?|\s|\"|$)|"
    r"/attendance/recognition/logs(?:\?|\s|\"|$)|"
    r"/attendance/lectures(?:/|\?|\s|\"|$)|"
    r"/attendance/report(?:/|\?|\s|\"|$)|"
    r"/health(?:/|\?|\s|\"|$)|"
    r"/health/attendance(?:\?|\s|\"|$)|"
    r"/health/surveillance(?:\?|\s|\"|$)|"
    r"/system/config(?:\?|\s|\"|$)|"
    r"/dashboard/attendance(?:/|\?|\s|\"|$)|"
    r"/api/sessions(?:/|\?|\s|\"|$)|"
    r"/telemetry/healthz(?:\?|\s|\"|$)"
    r")"
)

_POLLING_PATH_RE = re.compile(
    r"^(?:"
    r"/presence/sessions(?:/|$|\?)|"
    r"/attendance/evidence(?:/|$|\?)|"
    r"/attendance/classrooms/active(?:/|$|\?)|"
    r"/attendance/recognition/logs(?:/|$|\?)|"
    r"/attendance/lectures(?:/|$|\?)|"
    r"/attendance/report(?:/|$|\?)|"
    r"/health(?:/|$|\?)|"
    r"/system/config(?:/|$|\?)|"
    r"/dashboard/attendance(?:/|$|\?)|"
    r"/api/sessions(?:/|$|\?)|"
    r"/telemetry/healthz(?:/|$|\?)"
    r")"
)

_SUPPRESSED_INFO_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"event=(?:%s)\b" % "|".join(_READ_PATH_EVENTS)),
    re.compile(r"attendance states built\b"),
    re.compile(r"ingest session_id=.*received=\d+ persisted=\d+"),
)


def is_verbose_http() -> bool:
    """When true, emit non-polling HTTP access lines (HYBRID_VERBOSE_HTTP)."""
    raw = os.environ.get("HYBRID_VERBOSE_HTTP", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def is_polling_path(method: str, path: str) -> bool:
    """True for dashboard poll routes and CORS preflight."""
    if method.upper() == "OPTIONS":
        return True
    bare = path.split("?", 1)[0]
    if bare.endswith("/") and bare != "/":
        bare = bare.rstrip("/")
    return bool(_POLLING_PATH_RE.match(bare))


class _OperationalFormatter(logging.Formatter):
    """Compact console lines: 2026-05-27T12:00:00 [INFO] [ATTENDANCE] message."""

    _LEVEL_LABEL = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARN",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "ERROR",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        level = self._LEVEL_LABEL.get(record.levelno, record.levelname)
        tag = getattr(record, "ops_tag", None)
        msg = record.getMessage()
        if tag:
            return f"{ts} [{level}] [{tag}] {msg}"
        return f"{ts} [{level}] {msg}"


class _PipelineConsoleFilter(logging.Filter):
    """Drop repetitive polling noise; preserve warnings, errors, and pipeline events."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True

        msg = record.getMessage()

        if record.name == "uvicorn.access":
            return is_verbose_http() and not _POLLING_ACCESS_RE.search(msg)

        if record.name == "uvicorn" and _POLLING_ACCESS_RE.search(msg):
            return False

        for pattern in _SUPPRESSED_INFO_RES:
            if pattern.search(msg):
                return False

        for event in _READ_PATH_EVENTS:
            if f"event={event}" in msg:
                return False

        if "event=presence_ingested" in msg and "presence_event=heartbeat" in msg:
            return False

        return True


def configure_server_logging(*, level: int | str = logging.INFO) -> None:
    """Apply demo/research console logging for the composite cloud backend."""
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    formatter = _OperationalFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    console_filter = _PipelineConsoleFilter()

    logging.basicConfig(level=level, force=True)
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(console_filter)
    handler.setLevel(level)
    root.addHandler(handler)
    root.setLevel(level)

    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    if is_verbose_http():
        access_handler = logging.StreamHandler()
        access_handler.setFormatter(formatter)
        access_handler.addFilter(console_filter)
        access_handler.setLevel(logging.INFO)
        access.addHandler(access_handler)
        access.setLevel(logging.INFO)
    else:
        access.setLevel(logging.CRITICAL + 1)

    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.setLevel(logging.INFO)
        uv_logger.addFilter(console_filter)
