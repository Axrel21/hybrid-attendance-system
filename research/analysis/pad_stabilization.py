# research/analysis/pad_stabilization.py
"""PAD/liveness stabilization helpers (Task E of the runtime stabilization phase).

Compiles a single PAD stability score from existing pass-5/6 helpers
plus a replay-pattern detector. The runtime PAD code is untouched —
this module only consumes the diagnostic CSV.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from research.analysis.runtime_diagnostics import (
    pad_hysteresis,
    replay_pattern_diagnostics,
    rigid_ratio_temporal,
    spoof_transitions,
)
from research.analysis.stabilization import (
    load_diagnostic,
    pad_temporal_summary,
)


log = logging.getLogger("research.analysis.pad_stabilization")


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def pad_stability_score(df: pd.DataFrame) -> Dict[str, Any]:
    """Composite 0..1 score combining temporal, hysteresis, and transition signals.

    The score is designed so that a clean REAL-dominated session with
    few spoof transitions and a stable rigid_ratio scores ~1.0; a
    session full of REAL↔SPOOF flips scores low.
    """
    if df.empty:
        return {"score": 0.0, "components": {}, "missing_signals": ["empty"]}

    temporal = pad_temporal_summary(df)
    hyst = pad_hysteresis(df)
    transitions = spoof_transitions(df)
    rigid = rigid_ratio_temporal(df)

    components: Dict[str, Dict[str, float]] = {}
    missing: List[str] = []

    # 1. Real fraction (overall) — higher is more stable (genuine sessions)
    real_frac = (temporal.get("overall") or {}).get("real_fraction")
    if real_frac is None:
        missing.append("real_fraction")
    else:
        components["real_dominance"] = {
            "value": _clip01(real_frac), "weight": 0.30,
        }

    # 2. (1 - hysteresis flip rate)
    flip_rate = hyst.get("overall_flip_rate")
    if flip_rate is None:
        missing.append("flip_rate")
    else:
        components["flip_rate_inv"] = {
            "value": _clip01(1.0 - flip_rate), "weight": 0.25,
        }

    # 3. Mean per-track spoof-transition rate (lower is better)
    pt = transitions.get("per_track") or []
    if pt:
        avg_trans_rate = float(np.mean([r["flip_rate"] for r in pt]))
        components["transition_rate_inv"] = {
            "value": _clip01(1.0 - avg_trans_rate), "weight": 0.20,
        }
    else:
        missing.append("transition_rate")

    # 4. (1 - rigid_ratio per-track std mean) — clamped to a sensible scale.
    rt = rigid.get("per_track") or []
    if rt:
        avg_rr_std = float(np.mean([r["std"] for r in rt]))
        components["rigid_ratio_stability"] = {
            "value": _clip01(1.0 - (avg_rr_std / 0.30)), "weight": 0.15,
        }
    else:
        missing.append("rigid_ratio_stability")

    # 5. Replay-pattern flag — heuristic, surfaces in score only when significant.
    replay = replay_pattern_diagnostics(df)
    area_var_low = replay.get("area_var_below_100_rate") or 0.0
    components["replay_safety"] = {
        "value": _clip01(1.0 - area_var_low), "weight": 0.10,
    }

    total_w = sum(c["weight"] for c in components.values())
    if total_w == 0:
        return {"score": 0.0, "components": {}, "missing_signals": missing}
    weighted = sum(c["value"] * c["weight"] for c in components.values())
    score = weighted / total_w

    contributions = {
        name: {**c, "contribution": c["value"] * c["weight"]}
        for name, c in components.items()
    }
    return {
        "score": _clip01(score),
        "components": contributions,
        "missing_signals": missing,
        "detail": (
            "0..1 composite; higher = more PAD-stable. "
            "real_dominance + (1 - flip_rate) + (1 - transition_rate) + "
            "rigid_ratio stability + replay safety."
        ),
    }


def diagnose_session(diagnostic_csv: Path) -> Dict[str, Any]:
    df = load_diagnostic(Path(diagnostic_csv))
    return {
        "diagnostic_csv": str(diagnostic_csv),
        "rows": int(len(df)),
        "pad_stability_score": pad_stability_score(df),
        "pad_temporal": pad_temporal_summary(df),
        "spoof_transitions": spoof_transitions(df),
        "pad_hysteresis": pad_hysteresis(df),
        "rigid_ratio_temporal": rigid_ratio_temporal(df),
        "replay_pattern": replay_pattern_diagnostics(df),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument(
        "--out", default=None,
        help="output JSON path (default: <session>/summaries/pad_stabilization.json)",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    session_dir = Path(args.session)
    diag = session_dir / "diagnostics" / "diagnostic_log.csv"
    if not diag.exists():
        log.error("diagnostic CSV not found: %s", diag)
        return 2

    payload = diagnose_session(diag)
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "pad_stabilization.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info(
        "wrote %s (rows=%d, PAD stability=%.3f, missing=%s)",
        out_path, payload["rows"],
        payload["pad_stability_score"]["score"],
        payload["pad_stability_score"]["missing_signals"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
