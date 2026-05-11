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

Each launch creates an isolated session under experiments/exp_<timestamp>/ with
telemetry, diagnostics, debug_frames, plots, logs, config (settings snapshot),
and summaries. See config/experiment_session.py and os.environ["EXPERIMENT_ROOT"].

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

Log files (per session)
-----------------------
experiments/<exp_id>/diagnostics/attendance_log.csv
experiments/<exp_id>/diagnostics/diagnostic_log.csv
experiments/<exp_id>/telemetry/telemetry_log.csv
experiments/<exp_id>/debug_frames/         Optional JPEG dumps (if DEBUG_FRAMES=1).
experiments/<exp_id>/logs/run_<ts>.log     Run log for this process.
experiments/<exp_id>/config/settings_snapshot.json
"""
from __future__ import annotations

import logging
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)


def main() -> int:
    from config.experiment_session import init_experiment_session

    paths = init_experiment_session(PROJECT_ROOT)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(paths.run_log_path),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    log = logging.getLogger(__name__)

    log.info("Attendance pipeline starting.")
    log.info("Experiment session: %s", paths.experiment_id)
    log.info("Experiment root: %s", paths.root)
    log.info("Run log: %s", paths.run_log_path)

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
