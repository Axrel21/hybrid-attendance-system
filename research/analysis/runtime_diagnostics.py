# research/analysis/runtime_diagnostics.py
"""Gap-filling runtime diagnostics that complement
``research.analysis.stabilization``.

The pass-5 stabilization module covers the eight headline dimensions
(orientation, confidence, persistence, bbox, drift, blur/geometry, PAD
temporal, offload trigger, thermal). This module fills the smaller-but
-important gaps called out in the pass-6 brief:

* YuNet:  proximity warnings, missed-detection diagnostics,
          unstable-track diagnostics.
* Recognition:  identity flicker, track-level recognition summaries,
                orientation-vs-confidence binning.
* PAD:    rigid_ratio temporal stats, spoof transitions, replay-pattern
          (area-variance) detection, PAD label hysteresis.
* Orientation / geometry:  distance-vs-confidence binning,
                            frontal-vs-side confidence (alias of the
                            orientation binning).

Pure pandas. Reads the existing ``diagnostic_log.csv`` schema only —
no edge-runtime code changes.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from research.analysis.stabilization import _percentile_block, load_diagnostic

log = logging.getLogger("research.analysis.runtime_diagnostics")


# ── YuNet stabilization helpers ──────────────────────────────────────────────

def proximity_diagnostics(
    df: pd.DataFrame,
    near_min_buffer_m: float = 0.5,
    near_max_buffer_m: float = 0.5,
    min_distance_m: float = 0.4,
    max_distance_m: float = 3.0,
) -> Dict[str, Any]:
    """Fraction of frames close to MIN/MAX_DISTANCE bounds + OOR rate."""
    out: Dict[str, Any] = {"n": 0}
    if df.empty or "distance" not in df.columns:
        return out
    d = df["distance"].dropna().to_numpy(dtype=np.float64)
    if d.size == 0:
        return out
    near_min = float((d < (min_distance_m + near_min_buffer_m)).mean())
    near_max = float((d > (max_distance_m - near_max_buffer_m)).mean())
    out_of_range = float(((d < min_distance_m) | (d > max_distance_m)).mean())
    out.update({
        "n": int(d.size),
        "min_distance_m": min_distance_m,
        "max_distance_m": max_distance_m,
        "near_min_buffer_m": near_min_buffer_m,
        "near_max_buffer_m": near_max_buffer_m,
        "close_fraction": near_min,
        "far_fraction": near_max,
        "out_of_range_fraction": out_of_range,
        "distance_stats": _percentile_block(d),
    })
    return out


def missed_detection_diagnostics(df: pd.DataFrame) -> Dict[str, Any]:
    """Counts of NO_MATCH / OUT_OF_RANGE / NONE decisions."""
    out: Dict[str, Any] = {"n": 0}
    if df.empty or "decision" not in df.columns:
        return out
    out["n"] = int(len(df))
    counts = Counter(df["decision"].astype(str))
    miss = counts.get("NO_MATCH", 0)
    oor = counts.get("OUT_OF_RANGE", 0)
    nb = counts.get("NONE", 0)
    out["no_match_count"] = int(miss)
    out["no_match_rate"] = miss / out["n"]
    out["out_of_range_count"] = int(oor)
    out["out_of_range_rate"] = oor / out["n"]
    out["uncategorized_count"] = int(nb)
    out["decision_distribution"] = {k: int(v) for k, v in counts.items()}
    return out


def unstable_track_diagnostics(
    df: pd.DataFrame,
    min_frames_for_stable: int = 8,
) -> Dict[str, Any]:
    """Tracks shorter than ``min_frames_for_stable`` are flagged unstable."""
    out: Dict[str, Any] = {"n_tracks": 0, "unstable_tracks": []}
    if df.empty or "track_id" not in df.columns:
        return out
    short = []
    for tid, grp in df.groupby("track_id"):
        n = int(len(grp))
        if n < min_frames_for_stable:
            short.append({"track_id": int(tid) if pd.notna(tid) else None, "frames": n})
    out["n_tracks"] = int(df["track_id"].nunique())
    out["min_frames_for_stable"] = min_frames_for_stable
    out["unstable_tracks"] = short
    out["unstable_count"] = len(short)
    out["unstable_rate"] = (len(short) / out["n_tracks"]) if out["n_tracks"] else 0.0
    return out


# ── Recognition observability ────────────────────────────────────────────────

def identity_flicker(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track distinct-identity count. A stable track should see 1."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": [], "max_distinct": 0}
    if df.empty or "track_id" not in df.columns or "identity" not in df.columns:
        return out
    for tid, grp in df.groupby("track_id"):
        idents = grp["identity"].dropna().astype(str)
        idents = idents[idents != "NA"]
        distinct = idents.nunique()
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(len(grp)),
            "distinct_identities": int(distinct),
            "identity_counts": dict(Counter(idents)),
        })
    out["n_tracks"] = len(out["per_track"])
    out["max_distinct"] = int(max((p["distinct_identities"] for p in out["per_track"]), default=0))
    return out


def track_recognition_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track sim mean/std + dominant identity + recognised fraction."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    if df.empty or "track_id" not in df.columns or "sim" not in df.columns:
        return out
    for tid, grp in df.groupby("track_id"):
        sims = grp["sim"].dropna()
        idents = grp["identity"].dropna().astype(str) if "identity" in grp.columns else pd.Series(dtype=str)
        idents = idents[idents != "NA"]
        dominant = None
        dominant_frac = 0.0
        if not idents.empty:
            c = Counter(idents)
            dominant, dom_count = c.most_common(1)[0]
            dominant_frac = float(dom_count / len(idents))
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(len(grp)),
            "sim_mean": float(sims.mean()) if len(sims) else 0.0,
            "sim_std": float(sims.std()) if len(sims) > 1 else 0.0,
            "sim_p95": float(np.percentile(sims, 95)) if len(sims) else 0.0,
            "dominant_identity": dominant,
            "dominant_fraction": dominant_frac,
            "recognised_fraction": float((sims >= 0.65).mean()) if len(sims) else 0.0,
        })
    out["n_tracks"] = len(out["per_track"])
    return out


def orientation_vs_confidence(df: pd.DataFrame) -> Dict[str, Any]:
    """Mean / std / count of ``sim`` grouped by ``mode`` (FRONTAL/TILTED/OVERHEAD)."""
    out: Dict[str, Any] = {"n_modes": 0, "per_mode": []}
    if df.empty or "mode" not in df.columns or "sim" not in df.columns:
        return out
    for mode, grp in df.groupby("mode"):
        sims = grp["sim"].dropna().to_numpy(dtype=np.float64)
        if sims.size == 0:
            continue
        out["per_mode"].append({
            "mode": str(mode),
            "n": int(sims.size),
            "sim_mean": float(sims.mean()),
            "sim_std": float(sims.std()),
            "sim_p50": float(np.percentile(sims, 50)),
            "sim_p95": float(np.percentile(sims, 95)),
        })
    out["n_modes"] = len(out["per_mode"])
    return out


def distance_vs_confidence(
    df: pd.DataFrame,
    bins: List[float] = (0.4, 1.0, 1.5, 2.0, 2.5, 3.0),
) -> Dict[str, Any]:
    """Mean / std of ``sim`` per distance bin."""
    out: Dict[str, Any] = {"n_bins": 0, "per_bin": []}
    if df.empty or "distance" not in df.columns or "sim" not in df.columns:
        return out
    bins = list(bins)
    for lo, hi in zip(bins, bins[1:]):
        mask = (df["distance"] >= lo) & (df["distance"] < hi)
        sims = df.loc[mask, "sim"].dropna().to_numpy(dtype=np.float64)
        if sims.size == 0:
            continue
        out["per_bin"].append({
            "lo_m": float(lo),
            "hi_m": float(hi),
            "n": int(sims.size),
            "sim_mean": float(sims.mean()),
            "sim_std": float(sims.std()) if sims.size > 1 else 0.0,
        })
    out["n_bins"] = len(out["per_bin"])
    return out


# ── PAD observability ────────────────────────────────────────────────────────

def rigid_ratio_temporal(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track rigid_ratio time series stats."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    if df.empty or "track_id" not in df.columns or "rigid_ratio" not in df.columns:
        return out
    for tid, grp in df.groupby("track_id"):
        rr = grp["rigid_ratio"].dropna().to_numpy(dtype=np.float64)
        if rr.size == 0:
            continue
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "n": int(rr.size),
            "mean": float(rr.mean()),
            "std": float(rr.std()) if rr.size > 1 else 0.0,
            "p95": float(np.percentile(rr, 95)),
        })
    out["n_tracks"] = len(out["per_track"])
    return out


def spoof_transitions(df: pd.DataFrame) -> Dict[str, Any]:
    """REAL ↔ SPOOF (or → UNCERTAIN) transitions per track."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": [], "total_transitions": 0}
    if df.empty or "track_id" not in df.columns or "lbl" not in df.columns:
        return out
    total = 0
    for tid, grp in df.groupby("track_id"):
        labels = grp["lbl"].astype(str)
        flips = int((labels.shift() != labels).iloc[1:].sum()) if len(labels) > 1 else 0
        transitions = []
        if len(labels) > 1:
            for a, b in zip(labels.iloc[:-1], labels.iloc[1:]):
                if a != b:
                    transitions.append(f"{a}->{b}")
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(len(labels)),
            "flip_count": flips,
            "flip_rate": flips / max(1, len(labels) - 1),
            "transitions": dict(Counter(transitions)),
        })
        total += flips
    out["n_tracks"] = len(out["per_track"])
    out["total_transitions"] = int(total)
    return out


def replay_pattern_diagnostics(df: pd.DataFrame) -> Dict[str, Any]:
    """area_var and avg_blur statistics — proxy for still-frame / replay attacks."""
    out: Dict[str, Any] = {"n": 0}
    if df.empty:
        return out
    out["n"] = int(len(df))
    if "avg_area_var" in df.columns:
        a = df["avg_area_var"].dropna().to_numpy(dtype=np.float64)
        if a.size:
            out["area_var"] = _percentile_block(a)
            # Lower area_var means frame is "frozen" — replay candidate.
            # Use a soft <100 threshold (matches edge config heuristics).
            out["area_var_below_100_rate"] = float((a < 100).mean())
    if "avg_blur" in df.columns:
        b = df["avg_blur"].dropna().to_numpy(dtype=np.float64)
        if b.size:
            out["blur"] = _percentile_block(b)
            out["blur_below_80_rate"] = float((b < 80.0).mean())
    return out


def pad_hysteresis(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track PAD label flip rate (adjacent-frame ``lbl`` changes)."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": [], "overall_flip_rate": 0.0}
    if df.empty or "track_id" not in df.columns or "lbl" not in df.columns:
        return out
    overall_flips = 0
    overall_transitions = 0
    for tid, grp in df.groupby("track_id"):
        labels = grp["lbl"].astype(str)
        flips = int((labels.shift() != labels).iloc[1:].sum()) if len(labels) > 1 else 0
        transitions = max(0, len(labels) - 1)
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(len(labels)),
            "flip_count": flips,
            "flip_rate": flips / max(1, transitions),
        })
        overall_flips += flips
        overall_transitions += transitions
    out["n_tracks"] = len(out["per_track"])
    out["overall_flip_rate"] = overall_flips / max(1, overall_transitions)
    return out


# ── Top-level driver ─────────────────────────────────────────────────────────

def diagnose_session(diagnostic_csv: Path) -> Dict[str, Any]:
    df = load_diagnostic(diagnostic_csv)
    return {
        "diagnostic_csv": str(diagnostic_csv),
        "rows": int(len(df)),
        "proximity": proximity_diagnostics(df),
        "missed_detection": missed_detection_diagnostics(df),
        "unstable_tracks": unstable_track_diagnostics(df),
        "identity_flicker": identity_flicker(df),
        "track_recognition": track_recognition_summary(df),
        "orientation_vs_confidence": orientation_vs_confidence(df),
        "distance_vs_confidence": distance_vs_confidence(df),
        "rigid_ratio_temporal": rigid_ratio_temporal(df),
        "spoof_transitions": spoof_transitions(df),
        "replay_pattern": replay_pattern_diagnostics(df),
        "pad_hysteresis": pad_hysteresis(df),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument(
        "--out", default=None,
        help="output JSON path (default: <session>/summaries/runtime_diagnostics.json)",
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

    summary = diagnose_session(diag)
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "runtime_diagnostics.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s (rows=%d)", out_path, summary["rows"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
