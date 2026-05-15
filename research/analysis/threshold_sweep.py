# research/analysis/threshold_sweep.py
"""Offline threshold-sweep what-if analysis.

Consumes ``experiments/exp_<id>/diagnostics/diagnostic_log.csv`` and
simulates "what would have happened" under different recognition and
offload thresholds. Useful for calibrating ``MATCH_HIGH_BASE``,
``MATCH_MID_BASE``, and ``CLOUD_THRESHOLD`` without re-running the
camera.

Three views are produced:

1. **Match-threshold sweep** — over a range of ``th_high`` values, count
   how many frames would have decided MATCHED, OFFLOAD_TO_CLOUD,
   BELOW_THRESHOLD given the recorded ``sim`` per frame.
2. **Offload-threshold sweep** — over a range of ``th_mid`` values,
   count rows that would have triggered OFFLOAD_TO_CLOUD.
3. **Hysteresis diagnostics** — count decision flip-flops per track so
   the operator can see whether a wider mid/high gap would damp churn.

Pandas required (already in ``edge/requirements-edge.txt``).
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.analysis.stabilization import _percentile_block, load_diagnostic

log = logging.getLogger("research.analysis.threshold_sweep")


def match_threshold_sweep(
    df: pd.DataFrame,
    thresholds: np.ndarray,
    mid_offset: float = 0.0,
) -> List[Dict[str, Any]]:
    """For each ``th_high`` in ``thresholds``, count synthetic decisions.

    Uses the per-row ``sim`` column. The implicit ``th_mid`` = ``th_high
    - mid_offset`` so the existing two-tier decision logic is preserved.
    """
    if df.empty or "sim" not in df.columns:
        return [{"th_high": float(t), "n": 0} for t in thresholds]
    sims = df["sim"].dropna().to_numpy(dtype=np.float64)
    if sims.size == 0:
        return [{"th_high": float(t), "n": 0} for t in thresholds]
    out: List[Dict[str, Any]] = []
    n = int(sims.size)
    for th_high in thresholds:
        th_mid = max(0.0, float(th_high) - mid_offset)
        matched = int((sims >= th_high).sum())
        offload = int(((sims >= th_mid) & (sims < th_high)).sum())
        below = int((sims < th_mid).sum())
        out.append({
            "th_high": float(th_high),
            "th_mid": float(th_mid),
            "n": n,
            "matched_count": matched,
            "offload_count": offload,
            "below_threshold_count": below,
            "matched_rate": matched / n,
            "offload_rate": offload / n,
            "below_threshold_rate": below / n,
        })
    return out


def offload_threshold_sweep(
    df: pd.DataFrame,
    thresholds: np.ndarray,
    fixed_th_high: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """For each ``th_mid`` value, count rows that would offload."""
    if df.empty or "sim" not in df.columns:
        return [{"th_mid": float(t), "n": 0} for t in thresholds]
    sims = df["sim"].dropna().to_numpy(dtype=np.float64)
    if sims.size == 0:
        return [{"th_mid": float(t), "n": 0} for t in thresholds]
    n = int(sims.size)
    # If the operator did not pin th_high, use the maximum observed sim
    # as the upper bound for the offload band (the per-frame MATCHED
    # decisions already exceeded their recorded th_high; we recover
    # offload-band count as: sim in [th_mid, sim_max] minus matched).
    th_high = float(fixed_th_high) if fixed_th_high is not None else float(sims.max() + 1e-9)
    out: List[Dict[str, Any]] = []
    for th_mid in thresholds:
        offload = int(((sims >= float(th_mid)) & (sims < th_high)).sum())
        out.append({
            "th_mid": float(th_mid),
            "th_high": th_high,
            "n": n,
            "offload_count": offload,
            "offload_rate": offload / n,
        })
    return out


def hysteresis_diagnostics(df: pd.DataFrame) -> Dict[str, Any]:
    """Count adjacent-frame decision flip-flops per track."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    if df.empty or "decision" not in df.columns or "track_id" not in df.columns:
        return out
    overall_flips = 0
    overall_frames = 0
    for tid, grp in df.groupby("track_id"):
        decisions = grp["decision"].astype(str)
        flips = int((decisions.shift() != decisions).iloc[1:].sum()) if len(decisions) > 1 else 0
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(len(grp)),
            "flip_count": flips,
            "flip_rate": flips / max(1, len(grp) - 1),
            "decision_counts": dict(Counter(decisions)),
        })
        overall_flips += flips
        overall_frames += len(grp)
    out["n_tracks"] = len(out["per_track"])
    out["overall_flip_rate"] = (
        overall_flips / max(1, overall_frames - out["n_tracks"])
    )
    return out


def confidence_distribution(
    df: pd.DataFrame,
    column: str = "sim",
) -> Dict[str, Any]:
    """Percentile summary + coarse histogram for a numeric column."""
    if df.empty or column not in df.columns:
        return {"column": column, "n": 0}
    arr = df[column].dropna().to_numpy(dtype=np.float64)
    block = _percentile_block(arr)
    if arr.size:
        # Histogram in 20 bins between observed min/max — purely descriptive.
        hist, edges = np.histogram(arr, bins=20)
        block["histogram"] = {
            "counts": [int(x) for x in hist],
            "bin_edges": [float(e) for e in edges],
        }
    block["column"] = column
    return block


# ── Top-level driver ─────────────────────────────────────────────────────────

def sweep_session(
    diagnostic_csv: Path,
    th_high_range: Tuple[float, float] = (0.50, 0.95),
    th_mid_range: Tuple[float, float] = (0.40, 0.85),
    steps: int = 19,
    mid_offset: float = 0.15,
) -> Dict[str, Any]:
    df = load_diagnostic(diagnostic_csv)
    th_high_values = np.linspace(th_high_range[0], th_high_range[1], steps)
    th_mid_values = np.linspace(th_mid_range[0], th_mid_range[1], steps)
    return {
        "diagnostic_csv": str(diagnostic_csv),
        "rows": int(len(df)),
        "match_threshold_sweep": match_threshold_sweep(df, th_high_values, mid_offset=mid_offset),
        "offload_threshold_sweep": offload_threshold_sweep(df, th_mid_values),
        "hysteresis": hysteresis_diagnostics(df),
        "sim_distribution": confidence_distribution(df, "sim"),
        "live_conf_distribution": confidence_distribution(df, "live_conf") if "live_conf" in df.columns else {"column": "live_conf", "n": 0},
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument("--out", default=None,
                   help="output JSON path (default: <session>/summaries/threshold_sweep.json)")
    p.add_argument("--th-high-min", type=float, default=0.50)
    p.add_argument("--th-high-max", type=float, default=0.95)
    p.add_argument("--th-mid-min", type=float, default=0.40)
    p.add_argument("--th-mid-max", type=float, default=0.85)
    p.add_argument("--steps", type=int, default=19)
    p.add_argument("--mid-offset", type=float, default=0.15,
                   help="implicit th_mid = th_high - mid_offset for the match sweep")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    session_dir = Path(args.session)
    diag_csv = session_dir / "diagnostics" / "diagnostic_log.csv"
    if not diag_csv.exists():
        log.error("diagnostic CSV not found: %s", diag_csv)
        return 2

    summary = sweep_session(
        diag_csv,
        th_high_range=(args.th_high_min, args.th_high_max),
        th_mid_range=(args.th_mid_min, args.th_mid_max),
        steps=args.steps,
        mid_offset=args.mid_offset,
    )

    out_path = Path(args.out) if args.out else session_dir / "summaries" / "threshold_sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s (steps=%d, rows=%d)", out_path, args.steps, summary["rows"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
