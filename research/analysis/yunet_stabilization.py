# research/analysis/yunet_stabilization.py
"""YuNet stabilization helpers (Task C of the runtime stabilization phase).

Reference data showed:

* 88 short-lived tracks across 12 746 frames (~145 frames per track),
  meaning the detector frequently drops the face and the tracker
  re-acquires.
* Bbox area coefficient-of-variation per track is mostly in the
  0.05–0.20 range — small jitter, but jitter that turns into
  recognition volatility downstream because cropping shifts.
* `t_detect_ms` consumes 62 % of frame time; YuNet itself is the
  hottest stage.

This module ships **offline analyzers** only — the runtime detector is
untouched. The helpers expose what would happen if a smoothing step
were applied, and report bbox jitter / persistence / geometry quality
in a single bundle.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.analysis.stabilization import load_diagnostic, _percentile_block


log = logging.getLogger("research.analysis.yunet_stabilization")


# ── Bbox EMA simulator ───────────────────────────────────────────────────────

def ema_smooth(values: np.ndarray, alpha: float = 0.3) -> np.ndarray:
    """One-pole exponential moving average. ``alpha`` in (0, 1]."""
    if values.size == 0:
        return values
    out = np.empty_like(values, dtype=np.float64)
    out[0] = values[0]
    for i in range(1, values.size):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def bbox_jitter_simulation(
    df: pd.DataFrame,
    alpha: float = 0.3,
) -> Dict[str, Any]:
    """Per-track jitter quantification: raw vs EMA-smoothed face_w / face_h."""
    if df.empty or not {"track_id", "face_w", "face_h"}.issubset(df.columns):
        return {"n_tracks": 0, "per_track": []}

    rows: List[Dict[str, Any]] = []
    overall_w_red, overall_h_red = [], []
    for tid, grp in df.groupby("track_id"):
        w = pd.to_numeric(grp["face_w"], errors="coerce").dropna().to_numpy(dtype=np.float64)
        h = pd.to_numeric(grp["face_h"], errors="coerce").dropna().to_numpy(dtype=np.float64)
        if w.size < 4 or h.size < 4:
            continue
        ws = ema_smooth(w, alpha=alpha)
        hs = ema_smooth(h, alpha=alpha)
        raw_w_diff = np.abs(np.diff(w)).mean()
        raw_h_diff = np.abs(np.diff(h)).mean()
        smooth_w_diff = np.abs(np.diff(ws)).mean()
        smooth_h_diff = np.abs(np.diff(hs)).mean()
        w_red = 1.0 - (smooth_w_diff / raw_w_diff) if raw_w_diff > 0 else 0.0
        h_red = 1.0 - (smooth_h_diff / raw_h_diff) if raw_h_diff > 0 else 0.0
        rows.append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "n": int(min(w.size, h.size)),
            "raw_w_step_mean_px": float(raw_w_diff),
            "raw_h_step_mean_px": float(raw_h_diff),
            "smoothed_w_step_mean_px": float(smooth_w_diff),
            "smoothed_h_step_mean_px": float(smooth_h_diff),
            "w_jitter_reduction": float(w_red),
            "h_jitter_reduction": float(h_red),
        })
        overall_w_red.append(w_red)
        overall_h_red.append(h_red)
    return {
        "alpha": float(alpha),
        "n_tracks": len(rows),
        "per_track": rows,
        "mean_w_jitter_reduction": float(np.mean(overall_w_red)) if overall_w_red else 0.0,
        "mean_h_jitter_reduction": float(np.mean(overall_h_red)) if overall_h_red else 0.0,
        "detail": (
            "what EMA smoothing with alpha would do to bbox jitter. "
            "Reduction in mean |Δ| from the raw track signal."
        ),
    }


# ── Detection persistence + gap summary ──────────────────────────────────────

def persistence_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Per-track gap statistics: how often the detector loses the face."""
    if df.empty or "track_id" not in df.columns or "decision" not in df.columns:
        return {"n_tracks": 0, "per_track": []}
    rows: List[Dict[str, Any]] = []
    for tid, grp in df.groupby("track_id"):
        decisions = grp["decision"].astype(str).to_numpy()
        active = decisions != "NO_MATCH"
        if active.size == 0:
            continue
        # Build runs of inactive frames (gaps).
        gaps: List[int] = []
        cur = 0
        for v in active:
            if not v:
                cur += 1
            else:
                if cur > 0:
                    gaps.append(cur)
                cur = 0
        if cur > 0:
            gaps.append(cur)
        rows.append({
            "track_id": int(tid) if pd.notna(tid) else None,
            "frames": int(active.size),
            "active_frames": int(active.sum()),
            "active_fraction": float(active.mean()),
            "n_gaps": len(gaps),
            "mean_gap_frames": float(np.mean(gaps)) if gaps else 0.0,
            "max_gap_frames": int(max(gaps)) if gaps else 0,
            "p95_gap_frames": float(np.percentile(gaps, 95)) if gaps else 0.0,
        })
    overall_active = float(np.mean([r["active_fraction"] for r in rows])) if rows else 0.0
    overall_max_gap = int(max((r["max_gap_frames"] for r in rows), default=0))
    return {
        "n_tracks": len(rows),
        "mean_active_fraction": overall_active,
        "max_gap_frames_across_tracks": overall_max_gap,
        "per_track": rows,
    }


# ── Geometry / proximity / blur quality bundle ───────────────────────────────

def geometry_quality(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty or not {"face_w", "face_h"}.issubset(df.columns):
        return {"n": 0}
    w = pd.to_numeric(df["face_w"], errors="coerce").dropna().to_numpy(dtype=np.float64)
    h = pd.to_numeric(df["face_h"], errors="coerce").dropna().to_numpy(dtype=np.float64)
    if w.size == 0 or h.size == 0:
        return {"n": 0}
    area = w * h
    aspect = w / np.maximum(h, 1.0)
    return {
        "n": int(min(w.size, h.size)),
        "area": _percentile_block(area),
        "aspect_ratio": _percentile_block(aspect),
        "width": _percentile_block(w),
        "height": _percentile_block(h),
    }


def blur_quality(df: pd.DataFrame, sharp_floor: float = 80.0) -> Dict[str, Any]:
    if df.empty or "avg_blur" not in df.columns:
        return {"n": 0}
    b = pd.to_numeric(df["avg_blur"], errors="coerce").dropna().to_numpy(dtype=np.float64)
    if b.size == 0:
        return {"n": 0}
    return {
        "n": int(b.size),
        "sharp_floor": float(sharp_floor),
        "blur_stats": _percentile_block(b),
        "below_floor_fraction": float((b < sharp_floor).mean()),
        "detail": (
            f"avg_blur is Laplacian variance; values below {sharp_floor} "
            "are considered blurry. below_floor_fraction is the rate."
        ),
    }


def proximity_quality(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty or "distance" not in df.columns:
        return {"n": 0}
    d = pd.to_numeric(df["distance"], errors="coerce").dropna().to_numpy(dtype=np.float64)
    if d.size == 0:
        return {"n": 0}
    return {
        "n": int(d.size),
        "stats": _percentile_block(d),
        "close_fraction_under_0_9m": float((d < 0.9).mean()),
        "far_fraction_over_2_5m": float((d > 2.5).mean()),
    }


# ── Top-level driver ────────────────────────────────────────────────────────

def diagnose_session(diagnostic_csv: Path) -> Dict[str, Any]:
    df = load_diagnostic(Path(diagnostic_csv))
    return {
        "diagnostic_csv": str(diagnostic_csv),
        "rows": int(len(df)),
        "bbox_jitter_simulation_a0_30": bbox_jitter_simulation(df, alpha=0.30),
        "bbox_jitter_simulation_a0_50": bbox_jitter_simulation(df, alpha=0.50),
        "persistence": persistence_summary(df),
        "geometry_quality": geometry_quality(df),
        "blur_quality": blur_quality(df),
        "proximity_quality": proximity_quality(df),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument(
        "--out", default=None,
        help="output JSON path (default: <session>/summaries/yunet_stabilization.json)",
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
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "yunet_stabilization.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info(
        "wrote %s (rows=%d, tracks=%d, mean_w_jitter_reduction(a=0.3)=%.3f)",
        out_path, payload["rows"],
        payload["persistence"]["n_tracks"],
        payload["bbox_jitter_simulation_a0_30"]["mean_w_jitter_reduction"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
