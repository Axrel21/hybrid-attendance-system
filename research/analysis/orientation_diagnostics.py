# research/analysis/orientation_diagnostics.py
"""Orientation pipeline diagnostics (Task B of the runtime stabilization phase).

The pose estimator in :mod:`edge.orientation` is intentionally simple:
``ratio = vertical_dist / eye_dist`` mapped through two thresholds
(``ORIENTATION_OVERHEAD_TH`` / ``ORIENTATION_TILTED_TH``). Reference
data analysis surfaced three issues:

1. ``orient_ratio = 0.0`` is recorded as a sentinel whenever the pose
   estimator did not run that frame. Naïve percentile reads of the
   column report ``median = 0.0`` because ~70 % of rows are sentinels.
2. The configured ``OVERHEAD_TH = 0.60`` is below the observed
   minimum (~0.626). Across every captured session zero frames
   classify as ``OVERHEAD``.
3. Landmark mis-detections produce ratios >2 (max observed 6.86) —
   physically implausible. They inflate any analyzer that doesn't
   gate on a sanity range.

This helper does **not** modify the runtime classifier. It is a
sidecar analyzer that surfaces (1)–(3) explicitly and proposes
percentile-based threshold replacements. Operators apply them in
``config/settings.py`` if they want a different operating point.
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


log = logging.getLogger("research.analysis.orientation_diagnostics")

# Sane bounds for orient_ratio. Outside this range we treat the row as a
# landmark anomaly rather than a real pose. Empirically observed:
# valid frontal/tilted ratios live in [0.6, 1.8]; the tails are noise.
ANOMALY_MIN = 0.30
ANOMALY_MAX = 2.00


def _is_valid(df: pd.DataFrame) -> pd.Series:
    """Mask of rows where the pose estimator actually ran this frame."""
    if "mode_raw" in df.columns:
        return df["mode_raw"].notna() & (df["mode_raw"].astype(str) != "NA") & (df["mode_raw"].astype(str) != "")
    if "orient_ratio" in df.columns:
        return df["orient_ratio"].fillna(0.0) > 0
    return pd.Series([False] * len(df), index=df.index)


def valid_fraction(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"n": 0, "n_valid": 0, "valid_fraction": 0.0}
    valid = _is_valid(df)
    return {
        "n": int(len(df)),
        "n_valid": int(valid.sum()),
        "valid_fraction": float(valid.mean()),
        "detail": (
            "fraction of rows where the pose estimator produced a real ratio; "
            "sentinel zeros are filtered out"
        ),
    }


def landmark_anomaly_rate(
    df: pd.DataFrame,
    anomaly_min: float = ANOMALY_MIN,
    anomaly_max: float = ANOMALY_MAX,
) -> Dict[str, Any]:
    if df.empty or "orient_ratio" not in df.columns:
        return {"n_valid": 0, "anomaly_rate": 0.0}
    valid = _is_valid(df)
    if not valid.any():
        return {"n_valid": 0, "anomaly_rate": 0.0}
    ratios = df.loc[valid, "orient_ratio"].astype(float)
    out_of_band = (ratios < anomaly_min) | (ratios > anomaly_max)
    return {
        "n_valid": int(valid.sum()),
        "anomaly_min": float(anomaly_min),
        "anomaly_max": float(anomaly_max),
        "anomaly_count": int(out_of_band.sum()),
        "anomaly_rate": float(out_of_band.mean()),
        "detail": (
            f"ratios outside [{anomaly_min}, {anomaly_max}] indicate landmark "
            "mis-detections, not real pose changes"
        ),
    }


def overhead_reachability(
    df: pd.DataFrame,
    overhead_th: float = 0.60,
) -> Dict[str, Any]:
    """Fraction of valid frames at or below the OVERHEAD threshold."""
    if df.empty or "orient_ratio" not in df.columns:
        return {"reachable_fraction": 0.0, "n_valid": 0, "threshold": float(overhead_th)}
    valid = _is_valid(df)
    if not valid.any():
        return {"reachable_fraction": 0.0, "n_valid": 0, "threshold": float(overhead_th)}
    ratios = df.loc[valid, "orient_ratio"].astype(float)
    reachable = (ratios < overhead_th).mean()
    return {
        "n_valid": int(valid.sum()),
        "threshold": float(overhead_th),
        "min_observed_ratio": float(ratios.min()),
        "reachable_count": int((ratios < overhead_th).sum()),
        "reachable_fraction": float(reachable),
        "detail": (
            "fraction of valid frames where ratio < OVERHEAD_TH. "
            "If 0.0, the OVERHEAD bucket is unreachable with current geometry."
        ),
    }


def threshold_recommendation(
    df: pd.DataFrame,
    overhead_p: float = 0.10,
    tilted_p: float = 0.50,
) -> Dict[str, Any]:
    """Percentile-based suggestion for OVERHEAD_TH / TILTED_TH.

    Uses the valid-frames ratio distribution. The defaults treat
    ``overhead_p = 0.10`` as "10 % of frames are overhead" and
    ``tilted_p = 0.50`` as "50 % of frames are tilted or worse", which
    is roughly the operating-point hint in the existing
    ``analyze_orientation.py`` calibration suggestions.
    """
    if df.empty or "orient_ratio" not in df.columns:
        return {"detail": "no orient_ratio column"}
    valid = _is_valid(df)
    if valid.sum() < 50:
        return {"detail": f"insufficient valid samples ({int(valid.sum())})"}
    ratios = df.loc[valid, "orient_ratio"].astype(float)
    out: Dict[str, Any] = {
        "n_valid": int(valid.sum()),
        "overhead_percentile": float(overhead_p),
        "tilted_percentile": float(tilted_p),
        "suggested_overhead_th": float(np.quantile(ratios, overhead_p)),
        "suggested_tilted_th": float(np.quantile(ratios, tilted_p)),
        "current_min": float(ratios.min()),
        "current_max": float(ratios.max()),
        "detail": (
            "suggested thresholds are percentile cuts; review plot "
            "02_ratio_per_mode.png before applying. Move OVERHEAD_TH near "
            "the lower percentile so the bucket is reachable with this "
            "camera geometry."
        ),
    }
    return out


def ratio_distribution(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty or "orient_ratio" not in df.columns:
        return {"n_valid": 0}
    valid = _is_valid(df)
    if not valid.any():
        return {"n_valid": 0}
    ratios = df.loc[valid, "orient_ratio"].astype(float).to_numpy()
    return {"n_valid": int(valid.sum()), **_percentile_block(ratios)}


def raw_vs_smoothed_disagreement(df: pd.DataFrame) -> Dict[str, Any]:
    """Rate at which the smoothed ``mode`` differs from the raw ``mode_raw``."""
    if df.empty or "mode_raw" not in df.columns or "mode" not in df.columns:
        return {"n_valid": 0, "rate": 0.0}
    valid = _is_valid(df) & df["mode"].notna()
    if not valid.any():
        return {"n_valid": 0, "rate": 0.0}
    sub = df.loc[valid, ["mode", "mode_raw"]].astype(str)
    diff = (sub["mode"] != sub["mode_raw"]).mean()
    return {
        "n_valid": int(valid.sum()),
        "rate": float(diff),
        "detail": (
            "fraction of valid frames where the smoothed mode differs from "
            "the raw frame classification. If 0.0, smoothing is redundant."
        ),
    }


def diagnose_session(diagnostic_csv: Path) -> Dict[str, Any]:
    df = load_diagnostic(Path(diagnostic_csv))
    return {
        "diagnostic_csv": str(diagnostic_csv),
        "rows": int(len(df)),
        "valid_fraction": valid_fraction(df),
        "ratio_distribution": ratio_distribution(df),
        "landmark_anomaly": landmark_anomaly_rate(df),
        "overhead_reachability_at_0_60": overhead_reachability(df, 0.60),
        "raw_vs_smoothed_disagreement": raw_vs_smoothed_disagreement(df),
        "threshold_recommendation": threshold_recommendation(df),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument(
        "--out", default=None,
        help="output JSON path (default: <session>/summaries/orientation_diagnostics.json)",
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
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "orientation_diagnostics.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info(
        "wrote %s (rows=%d, valid_fraction=%.3f, overhead_reachable=%.3f)",
        out_path, payload["rows"],
        payload["valid_fraction"]["valid_fraction"],
        payload["overhead_reachability_at_0_60"]["reachable_fraction"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
