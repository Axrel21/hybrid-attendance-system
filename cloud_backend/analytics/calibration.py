# cloud_backend/analytics/calibration.py
"""Threshold / calibration metric helpers over the cloud event stream.

Counterparts to the offline tools in
``research.analysis.threshold_sweep``. Pure-function, numpy-only,
return ``{"n": 0, ...}`` on empty input.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np


def _to_float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if np.isnan(f):
        return None
    return f


def _collect_floats(events: Iterable[Dict[str, Any]], key: str) -> np.ndarray:
    vals: List[float] = []
    for ev in events:
        f = ev.get("fields") if isinstance(ev.get("fields"), dict) else {}
        v = _to_float(ev.get(key) or f.get(key))
        if v is not None:
            vals.append(v)
    return np.asarray(vals, dtype=np.float64)


def confidence_distribution(
    events: Iterable[Dict[str, Any]],
    key: str = "sim",
    bins: int = 20,
) -> Dict[str, Any]:
    """Percentile block + coarse histogram for one numeric field."""
    arr = _collect_floats(events, key)
    out: Dict[str, Any] = {"key": key, "n": int(arr.size)}
    if arr.size == 0:
        return out
    hist, edges = np.histogram(arr, bins=bins)
    out.update({
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=0)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "histogram": {
            "counts": [int(x) for x in hist],
            "bin_edges": [float(e) for e in edges],
        },
    })
    return out


def threshold_sweep(
    events: Iterable[Dict[str, Any]],
    th_high_values: Sequence[float],
    mid_offset: float = 0.15,
    sim_key: str = "sim",
) -> Dict[str, Any]:
    """For each ``th_high``, count synthetic MATCHED / OFFLOAD / BELOW decisions."""
    arr = _collect_floats(events, sim_key)
    points: List[Dict[str, Any]] = []
    n = int(arr.size)
    for th in th_high_values:
        th_high = float(th)
        th_mid = max(0.0, th_high - mid_offset)
        if n == 0:
            points.append({"th_high": th_high, "th_mid": th_mid, "n": 0})
            continue
        matched = int((arr >= th_high).sum())
        offload = int(((arr >= th_mid) & (arr < th_high)).sum())
        below = n - matched - offload
        points.append({
            "th_high": th_high,
            "th_mid": th_mid,
            "n": n,
            "matched_count": matched,
            "offload_count": offload,
            "below_threshold_count": below,
            "matched_rate": matched / n,
            "offload_rate": offload / n,
            "below_threshold_rate": below / n,
        })
    return {
        "n_events": n,
        "sim_key": sim_key,
        "mid_offset": mid_offset,
        "points": points,
    }


def hysteresis_count(
    events: Iterable[Dict[str, Any]],
    decision_key: str = "decision",
) -> Dict[str, Any]:
    """Adjacent-frame decision flip-flops per track."""
    by_track: Dict[Any, List[str]] = {}
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        f = ev.get("fields") if isinstance(ev.get("fields"), dict) else {}
        decision = ev.get(decision_key) or f.get(decision_key)
        if decision is None:
            continue
        by_track.setdefault(ev.get("track_id"), []).append(str(decision))
    rows = []
    total_flips = 0
    total_transitions = 0
    for tid, decisions in by_track.items():
        flips = 0
        for a, b in zip(decisions, decisions[1:]):
            if a != b:
                flips += 1
        total_flips += flips
        total_transitions += max(0, len(decisions) - 1)
        rows.append({
            "track_id": tid,
            "frames": len(decisions),
            "flip_count": flips,
            "flip_rate": flips / max(1, len(decisions) - 1),
            "decision_counts": dict(Counter(decisions)),
        })
    return {
        "n_tracks": len(rows),
        "overall_flip_rate": total_flips / max(1, total_transitions),
        "per_track": rows,
    }


__all__ = ["confidence_distribution", "threshold_sweep", "hysteresis_count"]
