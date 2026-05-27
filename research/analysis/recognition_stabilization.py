# research/analysis/recognition_stabilization.py
"""Recognition stabilization helpers (Task D of the runtime stabilization phase).

Reference data showed ``sim_std_mean_over_tracks`` ≈ 0.20–0.23 — high
enough that single-frame threshold decisions are inherently fragile.
This module simulates what an EMA on the ``sim`` channel would do to
that volatility and quantifies the resulting "would-have-matched"
profile across thresholds.

The runtime pipeline is unchanged. These helpers run offline.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from research.analysis.stabilization import load_diagnostic, _percentile_block
from research.analysis.yunet_stabilization import ema_smooth


log = logging.getLogger("research.analysis.recognition_stabilization")


def sim_volatility(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track sim mean / std / range, plus overall headline std."""
    if df.empty or "sim" not in df.columns or "track_id" not in df.columns:
        return {"n_tracks": 0, "per_track": []}
    rows: List[Dict[str, Any]] = []
    stds: List[float] = []
    for tid, grp in df.groupby("track_id"):
        s = pd.to_numeric(grp["sim"], errors="coerce").dropna()
        if len(s) < 2:
            continue
        rows.append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "n": int(len(s)),
            "sim_mean": float(s.mean()),
            "sim_std": float(s.std()),
            "sim_p95": float(np.percentile(s, 95)),
            "sim_min": float(s.min()),
            "sim_max": float(s.max()),
            "sim_range": float(s.max() - s.min()),
        })
        stds.append(float(s.std()))
    return {
        "n_tracks": len(rows),
        "sim_std_mean": float(np.mean(stds)) if stds else 0.0,
        "sim_std_p95": float(np.percentile(stds, 95)) if stds else 0.0,
        "per_track": rows,
    }


def sim_ema_simulation(df: pd.DataFrame, alpha: float = 0.3) -> Dict[str, Any]:
    """Quantify how much an EMA on ``sim`` would damp per-track volatility."""
    if df.empty or "sim" not in df.columns or "track_id" not in df.columns:
        return {"n_tracks": 0, "per_track": []}
    rows: List[Dict[str, Any]] = []
    reductions: List[float] = []
    for tid, grp in df.groupby("track_id"):
        s = pd.to_numeric(grp["sim"], errors="coerce").dropna().to_numpy(dtype=np.float64)
        if s.size < 3:
            continue
        s_smooth = ema_smooth(s, alpha=alpha)
        raw_std = float(s.std())
        sm_std = float(s_smooth.std())
        red = (1.0 - sm_std / raw_std) if raw_std > 0 else 0.0
        rows.append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "n": int(s.size),
            "raw_std": raw_std,
            "smoothed_std": sm_std,
            "std_reduction": red,
        })
        reductions.append(red)
    return {
        "alpha": float(alpha),
        "n_tracks": len(rows),
        "per_track": rows,
        "mean_std_reduction": float(np.mean(reductions)) if reductions else 0.0,
        "detail": (
            "EMA on the sim channel collapses single-frame swings. "
            "The runtime is unchanged; this is a what-if measurement."
        ),
    }


def matched_rate_at_thresholds(
    df: pd.DataFrame,
    thresholds: List[float] = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90),
    apply_ema: bool = False,
    alpha: float = 0.3,
) -> Dict[str, Any]:
    """Simulate matched_rate at each threshold, raw and (optionally) EMA-smoothed."""
    if df.empty or "sim" not in df.columns:
        return {"n": 0, "rows": []}
    sims = pd.to_numeric(df["sim"], errors="coerce").dropna().to_numpy(dtype=np.float64)
    if sims.size == 0:
        return {"n": 0, "rows": []}
    if apply_ema:
        sims = ema_smooth(sims, alpha=alpha)
    rows: List[Dict[str, Any]] = []
    for th in thresholds:
        th = float(th)
        m = int((sims >= th).sum())
        rows.append({
            "th_high": th,
            "matched_count": m,
            "matched_rate": m / sims.size,
        })
    return {
        "n": int(sims.size),
        "apply_ema": apply_ema,
        "alpha": float(alpha) if apply_ema else None,
        "rows": rows,
    }


def identity_persistence(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track longest run of MATCHED frames + dominant identity."""
    if df.empty or not {"track_id", "decision", "identity"}.issubset(df.columns):
        return {"n_tracks": 0, "per_track": []}
    rows: List[Dict[str, Any]] = []
    for tid, grp in df.groupby("track_id"):
        matched = (grp["decision"].astype(str) == "MATCHED").to_numpy(dtype=int)
        longest_run = 0
        cur = 0
        for v in matched:
            if v:
                cur += 1
                if cur > longest_run:
                    longest_run = cur
            else:
                cur = 0
        idents = grp.loc[grp["identity"].notna() & (grp["identity"].astype(str) != "NA"), "identity"]
        dom = None
        dom_frac = 0.0
        if not idents.empty:
            counts = idents.astype(str).value_counts()
            dom = counts.idxmax()
            dom_frac = float(counts.max() / counts.sum())
        rows.append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(len(grp)),
            "matched_count": int(matched.sum()),
            "longest_matched_run": int(longest_run),
            "dominant_identity": dom,
            "dominant_fraction": dom_frac,
        })
    return {"n_tracks": len(rows), "per_track": rows}


def diagnose_session(diagnostic_csv: Path) -> Dict[str, Any]:
    df = load_diagnostic(Path(diagnostic_csv))
    return {
        "diagnostic_csv": str(diagnostic_csv),
        "rows": int(len(df)),
        "sim_volatility": sim_volatility(df),
        "sim_ema_a0_20": sim_ema_simulation(df, alpha=0.20),
        "sim_ema_a0_30": sim_ema_simulation(df, alpha=0.30),
        "sim_ema_a0_50": sim_ema_simulation(df, alpha=0.50),
        "matched_rate_raw": matched_rate_at_thresholds(df, apply_ema=False),
        "matched_rate_ema_a0_30": matched_rate_at_thresholds(df, apply_ema=True, alpha=0.30),
        "identity_persistence": identity_persistence(df),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument(
        "--out", default=None,
        help="output JSON path (default: <session>/summaries/recognition_stabilization.json)",
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
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "recognition_stabilization.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info(
        "wrote %s (rows=%d, sim_std_mean=%.3f, EMA(0.3) reduction=%.2f)",
        out_path, payload["rows"],
        payload["sim_volatility"]["sim_std_mean"],
        payload["sim_ema_a0_30"]["mean_std_reduction"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
