"""
experiments/run_orientation_experiment.py
=========================================

Thin launcher for tagged orientation calibration sessions. Sets the
EXPERIMENT_LABEL environment variable so every diagnostic_log.csv row
written during this run carries that tag, then invokes the existing
edge.main pipeline. No pipeline behaviour is altered.

The launcher also prepends a one-line session-marker row to the log
(via a comment column? no — instead via a small JSON sidecar at
data/experiment_sessions.jsonl) so we have a durable record of when
each tag was started, what notes were attached, and which thresholds
were active. That file plays well with `analyze_orientation.py
--per-label` and the eventual research write-up.

Usage
-----
    # Frontal baseline at ~2 m
    python -m experiments.run_orientation_experiment frontal_2m \\
        --notes "user A, frontal, 2m, well-lit"

    # Overhead camera at 3 m
    python -m experiments.run_orientation_experiment overhead_3m

    # Suppress per-frame REC/DEBUG prints during the capture
    python -m experiments.run_orientation_experiment tilted_close --quiet

After capture, exit the runtime (press 'q' on the OpenCV window) and
analyse:

    python analyze_orientation.py --per-label
    python analyze_orientation.py --label overhead_3m
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
        "thresholds": {
            "ORIENTATION_OVERHEAD_TH": settings.ORIENTATION_OVERHEAD_TH,
            "ORIENTATION_TILTED_TH": settings.ORIENTATION_TILTED_TH,
            "ORIENTATION_SMOOTHING_WINDOW": settings.ORIENTATION_SMOOTHING_WINDOW,
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label",
                        help="experiment label, e.g. 'frontal_2m', 'overhead_3m', 'tilted_close'")
    parser.add_argument("--notes", default="",
                        help="free-form notes recorded into experiment_sessions.jsonl")
    parser.add_argument("--quiet", action="store_true",
                        help="silence per-frame [REC]/[DEBUG] prints (sets VERBOSE_DEBUG=0)")
    args = parser.parse_args()

    os.environ["EXPERIMENT_LABEL"] = args.label
    if args.quiet:
        os.environ["VERBOSE_DEBUG"] = "0"

    _record_session(args.label, args.notes)
    print(f"[EXPERIMENT] starting session label='{args.label}' "
          f"(quiet={args.quiet}); recorded to {SESSIONS_PATH}")

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
