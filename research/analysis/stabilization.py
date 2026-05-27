# research/analysis/stabilization.py
"""Offline stabilization diagnostics.

Consumes ``experiments/exp_<id>/diagnostics/diagnostic_log.csv`` (the
per-(frame, track) decision log produced by ``edge.main``) and emits a
single interpretable JSON summary capturing the eight stabilization
dimensions called out in the brief:

* orientation stability
* temporal confidence stability
* detection persistence / track consistency
* blur + geometry quality
* bounding-box stability
* recognition drift (per identity)
* PAD temporal behaviour
* offload trigger statistics

The CLI writes ``experiments/exp_<id>/summaries/stabilization.json`` so
the same artifact can be ingested by the cloud (uploader passes it
through ``/telemetry/sessions/end``'s ``summary``) or read by a notebook
without re-parsing the CSV.

Pandas is required (already in ``edge/requirements-edge.txt``). No edge
runtime code is modified; this module is consumed offline.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


log = logging.getLogger("research.analysis.stabilization")


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_diagnostic(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"diagnostic CSV not found: {path}")
    df = pd.read_csv(path)
    # Coerce numeric columns we depend on (CSV reader leaves blanks as NaN
    # but we want explicit numeric dtype for the analysis).
    for col in (
        "sim", "orient_ratio", "live_conf", "distance", "brightness",
        "face_w", "face_h", "avg_blur", "cpu_temp_c", "cpu_pct", "mem_mb",
        "fps_rolling", "t_detect_ms", "t_liveness_ms", "t_embed_ms",
        "t_match_ms", "cloud_rtt_ms", "jpeg_encode_ms", "th_high", "th_mid",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── Individual metric functions ──────────────────────────────────────────────

def orientation_stability(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track orientation churn + raw ratio variance."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    if df.empty or "track_id" not in df.columns:
        return out
    for tid, grp in df.groupby("track_id"):
        modes = grp.get("mode_raw") if "mode_raw" in grp.columns else grp.get("mode")
        if modes is None:
            continue
        modes_clean = modes.dropna().astype(str)
        flips = int((modes_clean.shift() != modes_clean).iloc[1:].sum()) if len(modes_clean) > 1 else 0
        ratios = grp.get("orient_ratio")
        ratio_std = float(ratios.std()) if ratios is not None and ratios.notna().sum() > 1 else 0.0
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(len(grp)),
            "mode_flip_count": flips,
            "mode_flip_rate": flips / max(1, len(modes_clean) - 1),
            "orient_ratio_std": ratio_std,
        })
    out["n_tracks"] = len(out["per_track"])
    out["mode_flip_rate_mean"] = float(
        np.mean([p["mode_flip_rate"] for p in out["per_track"]])
    ) if out["per_track"] else 0.0
    out["orient_ratio_std_mean"] = float(
        np.mean([p["orient_ratio_std"] for p in out["per_track"]])
    ) if out["per_track"] else 0.0
    return out


def confidence_stability(df: pd.DataFrame, window: int = 30) -> Dict[str, Any]:
    """Rolling std of ``sim`` per track (interpreted as confidence drift)."""
    out: Dict[str, Any] = {"n_tracks": 0, "window": window, "per_track": []}
    if df.empty or "sim" not in df.columns or "track_id" not in df.columns:
        return out
    for tid, grp in df.groupby("track_id"):
        sims = grp["sim"].dropna()
        if sims.empty:
            continue
        rolling = sims.rolling(window=window, min_periods=max(2, window // 5)).std()
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "n": int(len(sims)),
            "sim_mean": float(sims.mean()),
            "sim_std": float(sims.std()) if len(sims) > 1 else 0.0,
            "rolling_std_p95": float(np.nanpercentile(rolling, 95)) if rolling.notna().sum() else 0.0,
        })
    out["n_tracks"] = len(out["per_track"])
    out["sim_std_mean"] = float(np.mean([p["sim_std"] for p in out["per_track"]])) if out["per_track"] else 0.0
    return out


def detection_persistence(df: pd.DataFrame) -> Dict[str, Any]:
    """Persistence in frames per track: total frames + max contiguous run."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    if df.empty or "track_id" not in df.columns:
        return out
    for tid, grp in df.groupby("track_id"):
        decisions = grp.get("decision")
        if decisions is None:
            continue
        # Contiguous run length over frames where decision != NO_MATCH.
        active = (decisions != "NO_MATCH").astype(int).to_numpy()
        if active.size == 0:
            continue
        max_run = 0
        cur = 0
        for v in active:
            if v:
                cur += 1
                if cur > max_run:
                    max_run = cur
            else:
                cur = 0
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames_total": int(len(grp)),
            "frames_active": int(active.sum()),
            "max_run_active": int(max_run),
            "active_fraction": float(active.mean()),
        })
    out["n_tracks"] = len(out["per_track"])
    out["mean_active_fraction"] = float(
        np.mean([p["active_fraction"] for p in out["per_track"]])
    ) if out["per_track"] else 0.0
    return out


def bbox_stability(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track bounding-box stability: distance-derived geometry coefficient of variation."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    if df.empty or "track_id" not in df.columns:
        return out
    for tid, grp in df.groupby("track_id"):
        area = (grp.get("face_w").astype(float) * grp.get("face_h").astype(float)) \
            if {"face_w", "face_h"}.issubset(grp.columns) else None
        if area is None or area.dropna().empty:
            continue
        area_clean = area.dropna()
        mean = float(area_clean.mean())
        std = float(area_clean.std()) if len(area_clean) > 1 else 0.0
        cv = std / mean if mean > 0 else 0.0
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "n": int(len(area_clean)),
            "area_mean": mean,
            "area_std": std,
            "area_cv": cv,
        })
    out["n_tracks"] = len(out["per_track"])
    out["area_cv_mean"] = float(np.mean([p["area_cv"] for p in out["per_track"]])) if out["per_track"] else 0.0
    return out


def recognition_drift(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-identity similarity trend (slope of sim vs row index, normalized)."""
    out: Dict[str, Any] = {"n_identities": 0, "per_identity": []}
    if df.empty or "identity" not in df.columns or "sim" not in df.columns:
        return out
    rows = df[df["identity"].notna() & (df["identity"] != "NA")]
    if rows.empty:
        return out
    for ident, grp in rows.groupby("identity"):
        sims = grp["sim"].dropna()
        if len(sims) < 3:
            continue
        x = np.arange(len(sims), dtype=np.float64)
        y = sims.to_numpy(dtype=np.float64)
        if x.std() == 0:
            slope = 0.0
        else:
            slope = float(np.polyfit(x, y, 1)[0])
        out["per_identity"].append({
            "identity": str(ident),
            "n": int(len(sims)),
            "sim_mean": float(sims.mean()),
            "sim_slope_per_frame": slope,
        })
    out["n_identities"] = len(out["per_identity"])
    out["max_abs_slope"] = float(
        max((abs(p["sim_slope_per_frame"]) for p in out["per_identity"]), default=0.0)
    )
    return out


def blur_geometry_quality(df: pd.DataFrame) -> Dict[str, Any]:
    """Histograms-as-summary for avg_blur and face area."""
    out: Dict[str, Any] = {"n": 0}
    if df.empty:
        return out
    if "avg_blur" in df.columns:
        b = df["avg_blur"].dropna().to_numpy(dtype=np.float64)
        if b.size:
            out["blur"] = _percentile_block(b)
    if {"face_w", "face_h"}.issubset(df.columns):
        a = (df["face_w"].astype(float) * df["face_h"].astype(float)).dropna().to_numpy()
        if a.size:
            out["face_area"] = _percentile_block(a)
    if "distance" in df.columns:
        d = df["distance"].dropna().to_numpy(dtype=np.float64)
        if d.size:
            out["distance_m"] = _percentile_block(d)
    out["n"] = int(len(df))
    return out


def pad_temporal_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track PAD label fractions + REAL streak statistics."""
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": [], "overall": {}}
    if df.empty or "lbl" not in df.columns or "track_id" not in df.columns:
        return out
    overall = Counter()
    for tid, grp in df.groupby("track_id"):
        labels = grp["lbl"].astype(str)
        c = Counter(labels)
        total = int(sum(c.values()))
        real_arr = (labels == "REAL").to_numpy(dtype=int)
        if real_arr.size:
            max_real_run = _max_run(real_arr)
        else:
            max_real_run = 0
        out["per_track"].append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "n": total,
            "real_fraction": c["REAL"] / total if total else 0.0,
            "spoof_fraction": c["SPOOF"] / total if total else 0.0,
            "uncertain_fraction": c["UNCERTAIN"] / total if total else 0.0,
            "max_real_run": int(max_real_run),
        })
        overall.update(c)
    out["n_tracks"] = len(out["per_track"])
    total_overall = sum(overall.values())
    out["overall"] = {
        "real_fraction": overall["REAL"] / total_overall if total_overall else 0.0,
        "spoof_fraction": overall["SPOOF"] / total_overall if total_overall else 0.0,
        "uncertain_fraction": overall["UNCERTAIN"] / total_overall if total_overall else 0.0,
        "n": total_overall,
    }
    return out


def offload_trigger_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Offload event rate + outcome breakdown + edge/cloud agreement."""
    out: Dict[str, Any] = {"n": 0}
    if df.empty or "decision" not in df.columns:
        return out
    out["n"] = int(len(df))
    out["offload_trigger_count"] = int((df["decision"] == "OFFLOAD_TO_CLOUD").sum())
    out["offload_trigger_rate"] = (
        out["offload_trigger_count"] / out["n"] if out["n"] else 0.0
    )
    if "cloud_outcome" in df.columns:
        outcomes = df["cloud_outcome"].dropna().astype(str)
        out["outcome_counts"] = dict(Counter(outcomes))
    if "edge_cloud_agree" in df.columns:
        agree = df["edge_cloud_agree"].dropna()
        # CSV-stringified booleans show up as "True"/"False"; normalise.
        agree_bool = agree.astype(str).str.lower().isin(("true", "1"))
        non_null = agree_bool.size
        out["agreement_n"] = int(non_null)
        out["agreement_rate"] = float(agree_bool.mean()) if non_null else 0.0
    if "cloud_rtt_ms" in df.columns:
        rtt = df["cloud_rtt_ms"].dropna().to_numpy(dtype=np.float64)
        if rtt.size:
            out["rtt_ms"] = _percentile_block(rtt)
    return out


def thermal_summary(df: pd.DataFrame, threshold_c: float = 75.0) -> Dict[str, Any]:
    """CPU temperature percentiles + over-threshold rate."""
    out: Dict[str, Any] = {"n": 0}
    if df.empty or "cpu_temp_c" not in df.columns:
        return out
    t = df["cpu_temp_c"].dropna().to_numpy(dtype=np.float64)
    if t.size == 0:
        return out
    out["n"] = int(t.size)
    out.update(_percentile_block(t))
    out["threshold_c"] = float(threshold_c)
    out["over_threshold_frames"] = int((t >= threshold_c).sum())
    out["over_threshold_rate"] = float((t >= threshold_c).mean())
    return out


# ── Top-level driver ─────────────────────────────────────────────────────────

def summarize_session(diagnostic_csv: Path) -> Dict[str, Any]:
    df = load_diagnostic(diagnostic_csv)
    return {
        "diagnostic_csv": str(diagnostic_csv),
        "rows": int(len(df)),
        "orientation_stability": orientation_stability(df),
        "confidence_stability": confidence_stability(df),
        "detection_persistence": detection_persistence(df),
        "bbox_stability": bbox_stability(df),
        "recognition_drift": recognition_drift(df),
        "blur_geometry_quality": blur_geometry_quality(df),
        "pad_temporal": pad_temporal_summary(df),
        "offload_trigger": offload_trigger_summary(df),
        "thermal": thermal_summary(df),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _percentile_block(arr: np.ndarray) -> Dict[str, float]:
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=0)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


def _max_run(arr: np.ndarray) -> int:
    best = 0
    cur = 0
    for v in arr:
        if v:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--session", required=True,
        help="path to experiments/exp_<id>/",
    )
    p.add_argument(
        "--out", default=None,
        help="output JSON path (default: <session>/summaries/stabilization.json)",
    )
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

    summary = summarize_session(diag_csv)

    out_path = Path(args.out) if args.out else session_dir / "summaries" / "stabilization.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s (rows=%d, tracks=%d)", out_path,
             summary["rows"], summary["orientation_stability"]["n_tracks"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
