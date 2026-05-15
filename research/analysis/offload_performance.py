# research/analysis/offload_performance.py
"""Offload + CPU/cadence stabilization helpers (Task F).

Reference data showed YuNet detection consumes ~62 % of every frame
and that cadence (frame interval) is highly variable (p99/p50 ≈ 2.5×).
This helper turns those numbers into explicit measurement summaries
without changing any runtime behaviour.

It reads the per-(frame, track) diagnostic CSV for offload + decision
data and (optionally) the per-frame telemetry CSV for cadence / stage
share.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.analysis.stabilization import _percentile_block, load_diagnostic


log = logging.getLogger("research.analysis.offload_performance")


# ── Offload / routing diagnostics ────────────────────────────────────────────

def offload_volatility(df: pd.DataFrame, window: int = 60) -> Dict[str, Any]:
    """Rolling offload-trigger rate to surface bursts of offloads."""
    if df.empty or "decision" not in df.columns:
        return {"n": 0}
    series = (df["decision"].astype(str) == "OFFLOAD_TO_CLOUD").astype(int)
    if series.empty:
        return {"n": 0}
    window = max(2, min(window, len(series)))
    rolling = series.rolling(window=window, min_periods=max(2, window // 5)).mean()
    return {
        "n": int(len(series)),
        "window": int(window),
        "overall_rate": float(series.mean()),
        "rolling_rate_max": float(rolling.max(skipna=True)) if rolling.notna().any() else 0.0,
        "rolling_rate_p95": float(np.nanpercentile(rolling, 95)) if rolling.notna().any() else 0.0,
        "rolling_rate_std": float(rolling.std(skipna=True)) if rolling.notna().any() else 0.0,
        "detail": (
            "rolling fraction of frames triggering OFFLOAD_TO_CLOUD. "
            "Spikes mean bursty mid-confidence patterns."
        ),
    }


def threshold_boundary_diagnostics(
    df: pd.DataFrame,
    band_pct: float = 0.05,
) -> Dict[str, Any]:
    """Fraction of frames within ``band_pct`` of either threshold.

    Each diagnostic row records its active ``th_high`` / ``th_mid`` and the
    observed ``sim``. Anything inside ``th ± band`` is "boundary" — i.e.,
    a frame whose decision could flip with a tiny calibration tweak.
    """
    if df.empty or not {"sim", "th_high", "th_mid"}.issubset(df.columns):
        return {"n": 0}
    sim = pd.to_numeric(df["sim"], errors="coerce")
    th_high = pd.to_numeric(df["th_high"], errors="coerce")
    th_mid = pd.to_numeric(df["th_mid"], errors="coerce")
    # Exclude rows that never reached the recognition branch — those keep
    # the dbg-dict defaults (sim=0, th_high=0, th_mid=0) and would
    # spuriously count as "boundary" rows otherwise.
    valid = (
        sim.notna() & th_high.notna() & th_mid.notna()
        & (th_high > 0) & (th_mid > 0)
    )
    if not valid.any():
        return {"n": 0}
    s = sim[valid].to_numpy(dtype=np.float64)
    h = th_high[valid].to_numpy(dtype=np.float64)
    m = th_mid[valid].to_numpy(dtype=np.float64)
    near_high = np.abs(s - h) <= (h * band_pct)
    near_mid = np.abs(s - m) <= (m * band_pct)
    return {
        "n": int(valid.sum()),
        "band_pct": float(band_pct),
        "near_high_fraction": float(near_high.mean()),
        "near_mid_fraction": float(near_mid.mean()),
        "near_either_fraction": float((near_high | near_mid).mean()),
        "detail": (
            f"frames within ±{band_pct*100:.0f}% of the active threshold. "
            "High values mean calibration is touchy."
        ),
    }


# ── CPU / cadence diagnostics ────────────────────────────────────────────────

def cpu_hotspot_summary(telemetry_csv: Path) -> Dict[str, Any]:
    """Per-stage timing share over the frame loop.

    Reads telemetry CSV columns (``t_capture_ms``, ``t_detect_ms``, ...).
    Returns each stage's mean and percentage of total `t_total_ms`.
    """
    path = Path(telemetry_csv)
    if not path.exists() or path.stat().st_size == 0:
        return {"n": 0}
    tel = pd.read_csv(path)
    if tel.empty or "t_total_ms" not in tel.columns:
        return {"n": 0}
    total = pd.to_numeric(tel["t_total_ms"], errors="coerce").dropna()
    if total.empty:
        return {"n": 0}
    sum_total = float(total.sum())
    stages = (
        "t_capture_ms", "t_detect_ms", "t_tracks_ms",
        "t_liveness_max_ms", "t_embed_max_ms", "t_match_max_ms",
        "t_overlay_ms", "t_post_ms",
    )
    rows: List[Dict[str, Any]] = []
    for stage in stages:
        if stage not in tel.columns:
            continue
        s = pd.to_numeric(tel[stage], errors="coerce").dropna()
        if s.empty:
            continue
        rows.append({
            "stage": stage,
            "n": int(s.size),
            "mean_ms": float(s.mean()),
            "p95_ms": float(np.percentile(s, 95)),
            "share_of_total": float(s.sum() / sum_total) if sum_total > 0 else 0.0,
        })
    rows.sort(key=lambda r: r["share_of_total"], reverse=True)
    return {
        "n": int(total.size),
        "total_ms_mean": float(total.mean()),
        "total_ms_p95": float(np.percentile(total, 95)),
        "rows": rows,
        "detail": (
            "stage shares are summed over the run; t_tracks_ms wraps the "
            "per-track loop so liveness/embed/match shares add up beyond "
            "100% relative to a single frame."
        ),
    }


def cadence_summary(telemetry_csv: Path) -> Dict[str, Any]:
    path = Path(telemetry_csv)
    if not path.exists() or path.stat().st_size == 0:
        return {"n": 0}
    tel = pd.read_csv(path)
    if "dt_ms" not in tel.columns:
        return {"n": 0}
    dt = pd.to_numeric(tel["dt_ms"], errors="coerce").dropna().to_numpy(dtype=np.float64)
    if dt.size == 0:
        return {"n": 0}
    block = _percentile_block(dt)
    return {
        "n": int(dt.size),
        **block,
        "fps_mean": (1000.0 / float(block["mean"])) if block["mean"] > 0 else 0.0,
        "fps_p50": (1000.0 / float(block["p50"])) if block["p50"] > 0 else 0.0,
        "cv": (block["std"] / block["mean"]) if block["mean"] > 0 else 0.0,
        "detail": (
            "frame-interval distribution from the telemetry log. cv > 0.2 "
            "indicates noticeable cadence jitter."
        ),
    }


# ── Top-level driver ────────────────────────────────────────────────────────

def diagnose_session(session_dir: Path) -> Dict[str, Any]:
    session_dir = Path(session_dir)
    diag = session_dir / "diagnostics" / "diagnostic_log.csv"
    tel = session_dir / "telemetry" / "telemetry_log.csv"
    payload: Dict[str, Any] = {
        "session_dir": str(session_dir),
        "rows": 0,
    }
    if diag.exists():
        df = load_diagnostic(diag)
        payload["rows"] = int(len(df))
        payload["offload_volatility"] = offload_volatility(df)
        payload["threshold_boundary"] = threshold_boundary_diagnostics(df)
    else:
        payload["detail"] = "diagnostic CSV missing"
    if tel.exists():
        payload["cpu_hotspots"] = cpu_hotspot_summary(tel)
        payload["cadence"] = cadence_summary(tel)
    else:
        payload["cpu_hotspots"] = {"n": 0}
        payload["cadence"] = {"n": 0}
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument(
        "--out", default=None,
        help="output JSON path (default: <session>/summaries/offload_performance.json)",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    session_dir = Path(args.session)
    payload = diagnose_session(session_dir)
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "offload_performance.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    cadence = payload.get("cadence") or {}
    cpu = payload.get("cpu_hotspots") or {}
    log.info(
        "wrote %s (rows=%d, cadence p50=%s, top stage=%s)",
        out_path, payload["rows"],
        cadence.get("p50"),
        (cpu.get("rows") or [{}])[0].get("stage"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
