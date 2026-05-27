# config/logging_setup.py
"""
Structured logging for experiment sessions.

Tiers:
  * Console       — WARNING+ and selective INFO (startup-style messages only)
  * runtime.log   — operational INFO+ (attendance, camera, diagnostics)
  * debug.log     — per-frame / verbose diagnostics when VERBOSE_DEBUG=1

Telemetry and diagnostic CSVs are unchanged (written by edge.main).
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

from config.experiment_session import ExperimentPaths, get_current_paths

_CONFIGURED = False

# Loggers to use in application code:
#   attendance.runtime — business / ops (also echoed to console when INFO)
#   attendance.debug   — high-volume frame-level detail (file only)
LOG_RUNTIME = logging.getLogger("attendance.runtime")
LOG_DEBUG = logging.getLogger("attendance.debug")


class _ConsoleFilter(logging.Filter):
    """Emit WARNING+ always; INFO only from approved low-volume loggers."""

    _INFO_OK = frozenset({"attendance.runtime", "attendance.run"})

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        if record.name in self._INFO_OK and record.levelno >= logging.INFO:
            return True
        return False


def configure_session_logging(paths: ExperimentPaths, verbose_debug: bool) -> None:
    """Attach handlers for this process. Safe to call once per session."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root_attendance = logging.getLogger("attendance")
    root_attendance.handlers.clear()
    root_attendance.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fmt_short = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh_run = logging.FileHandler(paths.runtime_log_path, encoding="utf-8")
    fh_run.setLevel(logging.INFO)
    fh_run.setFormatter(fmt)
    root_attendance.addHandler(fh_run)

    fh_dbg = logging.FileHandler(paths.debug_log_path, encoding="utf-8")
    fh_dbg.setLevel(logging.DEBUG)
    fh_dbg.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt_short)
    ch.addFilter(_ConsoleFilter())
    root_attendance.addHandler(ch)
    root_attendance.propagate = False

    dbg = logging.getLogger("attendance.debug")
    dbg.setLevel(logging.DEBUG if verbose_debug else logging.WARNING)
    dbg.handlers.clear()
    dbg.addHandler(fh_dbg)
    dbg.propagate = False

    logging.getLogger("attendance.run").setLevel(logging.INFO)
    logging.getLogger("attendance.runtime").setLevel(logging.INFO)

    _CONFIGURED = True


def ensure_session_logging(verbose_debug: bool) -> None:
    """If run from edge.main without run.py, configure using current experiment paths."""
    paths: Optional[ExperimentPaths] = get_current_paths()
    if paths is None:
        return
    configure_session_logging(paths, verbose_debug)
