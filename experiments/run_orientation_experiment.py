"""
experiments/run_orientation_experiment.py
=========================================

Thin launcher for tagged orientation calibration sessions. Sets the
EXPERIMENT_LABEL environment variable so every diagnostic_log.csv row
written during this run carries that tag, then invokes the existing
edge.main pipeline. No pipeline behaviour is altered.

The launcher also writes a session-marker record to
data/experiment_sessions.jsonl (label, notes, timestamps, and the
active threshold values) so different capture sessions are always
traceable in offline analysis.

Usage — development laptop (webcam, display)
--------------------------------------------
    python -m experiments.run_orientation_experiment frontal_2m \\
        --notes "user A, frontal, 2m, well-lit"

    python -m experiments.run_orientation_experiment overhead_3m

    # Suppress per-frame REC/DEBUG prints during a long capture
    python -m experiments.run_orientation_experiment tilted_close --quiet

Usage — Raspberry Pi with Pi Camera Module 2
--------------------------------------------
    CAMERA_BACKEND=picamera2 HEADLESS=1 \\
    python -m experiments.run_orientation_experiment pi_frontal_2m \\
        --notes "Pi Camera, overhead mount, frontal, 2m" --quiet

    CAMERA_BACKEND=picamera2 HEADLESS=1 \\
    python -m experiments.run_orientation_experiment pi_overhead_3m \\
        --notes "Pi Camera, overhead mount, 45-deg tilt, 3m" --quiet

Pi Camera K_FOCAL calibration (run this BEFORE the first Pi session)
----------------------------------------------------------------------
1.  Mount the Pi Camera Module 2 at its final position.
2.  Run a frontal session with one person standing exactly 2.0 m away.
3.  In the diagnostic_log.csv, filter rows where identity is that person
    and mode_raw == 'FRONTAL'.  Read the median face_w and face_h.
4.  New K_FOCAL = 2.0 * sqrt(median_face_w * median_face_h)
5.  Update config/settings.py: K_FOCAL = <new value>
6.  Re-run all calibration sessions with the updated K_FOCAL.

Offline analysis after capture
-------------------------------
    # Orientation threshold calibration
    python analyze_orientation.py --per-label
    python analyze_orientation.py --label pi_overhead_3m

    # Performance baseline (Pi sessions only)
    python analyze_pi_perf.py --per-label
    python analyze_pi_perf.py --label pi_frontal_2m
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
SESSIONS_PATH = os.path.join(PROJECT_ROOT, "data", "experiment_sessions.jsonl")


def _record_session(label: str, notes: str) -> None:
    """Append a session marker so we have a durable index of captures."""
    os.makedirs(os.path.dirname(SESSIONS_PATH), exist_ok=True)

    # Lazy import so this script can be inspected without TF installed.
    sys.path.insert(0, PROJECT_ROOT)
    from config import settings  # noqa: E402

    record = {
        "label": label,
        "notes": notes,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "camera_backend": settings.CAMERA_BACKEND,
        "headless": settings.HEADLESS,
        "thresholds": {
            "ORIENTATION_OVERHEAD_TH":    settings.ORIENTATION_OVERHEAD_TH,
            "ORIENTATION_TILTED_TH":      settings.ORIENTATION_TILTED_TH,
            "ORIENTATION_SMOOTHING_WINDOW": settings.ORIENTATION_SMOOTHING_WINDOW,
            "MATCH_HIGH_BASE":            settings.MATCH_HIGH_BASE,
            "MATCH_MID_BASE":             settings.MATCH_MID_BASE,
            "MIN_DISTANCE":               settings.MIN_DISTANCE,
            "MAX_DISTANCE":               settings.MAX_DISTANCE,
            "K_FOCAL":                    settings.K_FOCAL,
        },
    }
    with open(SESSIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label",
                        help="experiment label, e.g. 'frontal_2m', 'overhead_3m', 'tilted_close'")
    parser.add_argument("--notes", default="",
                        help="free-form notes recorded into experiment_sessions.jsonl")
    parser.add_argument("--quiet", action="store_true",
                        help="silence per-frame [REC]/[DEBUG] prints (sets VERBOSE_DEBUG=0)")
    parser.add_argument("--headless", action="store_true",
                        help="skip cv2.imshow (sets HEADLESS=1); use on Pi over SSH")
    parser.add_argument("--pi-camera", action="store_true",
                        help="use Picamera2 backend (sets CAMERA_BACKEND=picamera2)")
    args = parser.parse_args()

    os.environ["EXPERIMENT_LABEL"] = args.label
    if args.quiet:
        os.environ["VERBOSE_DEBUG"] = "0"
    if args.headless:
        os.environ["HEADLESS"] = "1"
    if args.pi_camera:
        os.environ["CAMERA_BACKEND"] = "picamera2"

    _record_session(args.label, args.notes)
    print(f"[EXPERIMENT] starting session label='{args.label}' "
          f"(quiet={args.quiet}, headless={args.headless}, "
          f"pi_camera={args.pi_camera}); recorded to {SESSIONS_PATH}")

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
