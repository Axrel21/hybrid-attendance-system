#!/usr/bin/env python3
"""
run.py — top-level entry point for the attendance edge pipeline.

Usage
-----
Development (laptop):
    python run.py

Raspberry Pi (manual):
    source ~/attendance/venv/bin/activate
    python run.py

Raspberry Pi (systemd service, see deployment/attendance.service):
    sudo systemctl start attendance

Environment flags
-----------------
HEADLESS=1          Skip all cv2.imshow / display calls (SSH / service mode).
STREAM_VIDEO=1      Optional Flask MJPEG (remote); requires `pip install flask`.
STREAM_PORT         MJPEG listen port (default 5000).
TELEMETRY=0         Disable frame telemetry CSV + HUD strip (track timing remains in diagnostic_log).
DEBUG_FRAMES=1      Optional event JPEG capture (rate-limited); GUI: press 's' for manual snapshot.
CAMERA_BACKEND      "opencv" (default) or Pi backends (e.g. libcamera_subprocess).
VERBOSE_DEBUG=0     Silence per-frame console prints.
EXPERIMENT_LABEL    Tag all diagnostic rows with a label for offline analysis.

Log files
---------
data/attendance_log.csv     Matched attendance events only.
data/diagnostic_log.csv     Per-frame per-track diagnostic data.
data/telemetry_log.csv      Per-frame pipeline timing / FPS / thermal (if TELEMETRY=1).
debug_frames/               Optional JPEG dumps (if DEBUG_FRAMES=1).
logs/run_<timestamp>.log    Runtime exception and startup log.
"""
from __future__ import annotations

import logging
import os
import sys
import time

# ------------------------------------------------------------------
# Logging — both file (logs/) and stdout so systemd journald captures it
# ------------------------------------------------------------------
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

_log_file = os.path.join(_LOG_DIR, f"run_{time.strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Ensure the project root is on sys.path regardless of CWD
# ------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    log.info("Attendance pipeline starting.")
    log.info("Log file: %s", _log_file)

    # Surface the active configuration so the log captures the exact
    # settings used in this run — useful for correlating performance
    # data with config changes.
    try:
        from config import settings
        log.info(
            "Config: SIMULATE_PI=%s  HEADLESS=%s  STREAM_VIDEO=%s  TELEMETRY=%s  DEBUG_FRAMES=%s"
            "  CAMERA_BACKEND=%s  VERBOSE_DEBUG=%s  EXPERIMENT_LABEL=%r",
            settings.SIMULATE_PI,
            settings.HEADLESS,
            settings.STREAM_VIDEO,
            settings.TELEMETRY,
            settings.DEBUG_FRAMES,
            settings.CAMERA_BACKEND,
            settings.VERBOSE_DEBUG,
            settings.EXPERIMENT_LABEL,
        )
        if settings.STREAM_VIDEO:
            log.info(
                "MJPEG: http://0.0.0.0:%s/video_feed (use device LAN IP in browser)",
                settings.STREAM_PORT,
            )
    except Exception:
        log.exception("Failed to load config.settings — aborting.")
        return 1

    try:
        from edge.main import FinalHybridEdge
        node = FinalHybridEdge()
        node.run()
        log.info("Pipeline exited cleanly.")
        return 0

    except KeyboardInterrupt:
        log.info("Interrupted by keyboard (SIGINT).")
        return 0

    except Exception:
        log.exception("Unhandled exception — pipeline exited abnormally.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
