"""
research/experiments/orientation_launcher.py
===========================================

Tagged orientation calibration sessions. Sets ``EXPERIMENT_LABEL`` so every
diagnostic row carries the label, then runs ``edge.main.FinalHybridEdge``.

Also appends a marker to ``data/experiment_sessions.jsonl``.

Usage — development laptop (webcam, display)
--------------------------------------------
    python -m experiments.run_orientation_experiment frontal_2m \\
        --notes "user A, frontal, 2m, well-lit"

    python -m experiments.run_orientation_experiment overhead_3m

Usage — Raspberry Pi
--------------------
    CAMERA_BACKEND=libcamera HEADLESS=1 \\
    python -m experiments.run_orientation_experiment pi_frontal_2m --quiet

Offline analysis after capture
------------------------------
    python analyze_orientation.py --per-label
    python analyze_pi_perf.py --per-label

(``analyze_*.py`` at repo root are thin shims into ``research.analysis``.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", ".."))
SESSIONS_PATH = os.path.join(PROJECT_ROOT, "data", "experiment_sessions.jsonl")


def _record_session(label: str, notes: str) -> None:
    """Append a session marker so we have a durable index of captures."""
    os.makedirs(os.path.dirname(SESSIONS_PATH), exist_ok=True)

    sys.path.insert(0, PROJECT_ROOT)
    from config import settings  # noqa: E402

    record = {
        "label": label,
        "notes": notes,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "camera_backend": settings.CAMERA_BACKEND,
        "headless": settings.HEADLESS,
        "thresholds": {
            "ORIENTATION_OVERHEAD_TH": settings.ORIENTATION_OVERHEAD_TH,
            "ORIENTATION_TILTED_TH": settings.ORIENTATION_TILTED_TH,
            "ORIENTATION_SMOOTHING_WINDOW": settings.ORIENTATION_SMOOTHING_WINDOW,
            "POSE_TELEMETRY_MIN_IOU": settings.POSE_TELEMETRY_MIN_IOU,
            "MATCH_HIGH_BASE": settings.MATCH_HIGH_BASE,
            "MATCH_MID_BASE": settings.MATCH_MID_BASE,
            "MIN_DISTANCE": settings.MIN_DISTANCE,
            "MAX_DISTANCE": settings.MAX_DISTANCE,
            "K_FOCAL": settings.K_FOCAL,
        },
    }
    with open(SESSIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "label",
        help="experiment label, e.g. 'frontal_2m', 'overhead_3m', 'tilted_close'",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="free-form notes recorded into experiment_sessions.jsonl",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="silence per-frame [REC]/[DEBUG] prints (sets VERBOSE_DEBUG=0)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="skip cv2.imshow (sets HEADLESS=1); use on Pi over SSH",
    )
    parser.add_argument(
        "--pi-camera",
        action="store_true",
        help="use Picamera2 backend (sets CAMERA_BACKEND=picamera2)",
    )
    args = parser.parse_args()

    os.environ["EXPERIMENT_LABEL"] = args.label
    if args.quiet:
        os.environ["VERBOSE_DEBUG"] = "0"
    if args.headless:
        os.environ["HEADLESS"] = "1"
    if args.pi_camera:
        os.environ["CAMERA_BACKEND"] = "picamera2"

    _record_session(args.label, args.notes)
    print(
        f"[EXPERIMENT] starting session label='{args.label}' "
        f"(quiet={args.quiet}, headless={args.headless}, "
        f"pi_camera={args.pi_camera}); recorded to {SESSIONS_PATH}"
    )

    sys.path.insert(0, PROJECT_ROOT)
    from edge.main import FinalHybridEdge  # noqa: E402

    node = FinalHybridEdge()
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
