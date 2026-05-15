"""
Backward-compatible entry point for tagged orientation sessions.

Full documentation: ``research/experiments/orientation_launcher.py``.

    python -m experiments.run_orientation_experiment <label> [options]
"""
from __future__ import annotations

from research.experiments.orientation_launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
