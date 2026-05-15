# cloud_backend/analytics/stabilization.py
"""Stabilization metric helpers over the cloud event stream.

Mirrors the offline ``research.analysis.stabilization`` helpers but
consumes the JSONL event stream produced by
``/telemetry/ingest`` — i.e. each input is a list of event dicts whose
``fields`` map contains the same per-frame data the edge wrote to
``diagnostic_log.csv``.

Pure functions, numpy required, pandas not imported. All helpers return
``{"n": 0, ...}`` on empty input so the dashboard router can render
"no data" rather than crash.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

import numpy as np


def _f(ev: Dict[str, Any]) -> Dict[str, Any]:
    fields = ev.get("fields")
    return fields if isinstance(fields, dict) else {}


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


# ── Orientation ───────────────────────────────────────────────────────────────

def orientation_stability(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Mode flip rate per track + orient_ratio dispersion."""
    per_track: Dict[Any, Dict[str, Any]] = defaultdict(
        lambda: {"modes": [], "ratios": []}
    )
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        f = _f(ev)
        tid = ev.get("track_id")
        mode = f.get("mode_raw") or f.get("mode")
        if mode:
            per_track[tid]["modes"].append(str(mode))
        ratio = _to_float(f.get("orient_ratio"))
        if ratio is not None:
            per_track[tid]["ratios"].append(ratio)

    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    flip_rates: List[float] = []
    ratio_stds: List[float] = []
    for tid, payload in per_track.items():
        modes = payload["modes"]
        flips = 0
        for a, b in zip(modes, modes[1:]):
            if a != b:
                flips += 1
        denom = max(1, len(modes) - 1)
        flip_rate = flips / denom
        ratios = np.asarray(payload["ratios"], dtype=np.float64) if payload["ratios"] else np.empty(0)
        ratio_std = float(ratios.std(ddof=0)) if ratios.size > 1 else 0.0
        out["per_track"].append({
            "track_id": tid,
            "mode_flip_count": flips,
            "mode_flip_rate": flip_rate,
            "orient_ratio_std": ratio_std,
            "n_modes": len(modes),
        })
        flip_rates.append(flip_rate)
        ratio_stds.append(ratio_std)
    out["n_tracks"] = len(out["per_track"])
    out["mode_flip_rate_mean"] = float(np.mean(flip_rates)) if flip_rates else 0.0
    out["orient_ratio_std_mean"] = float(np.mean(ratio_stds)) if ratio_stds else 0.0
    return out


# ── Confidence ────────────────────────────────────────────────────────────────

def confidence_stability(
    events: Iterable[Dict[str, Any]],
    window: int = 30,
) -> Dict[str, Any]:
    """Rolling std of ``sim`` per track."""
    per_track: Dict[Any, List[float]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        v = _to_float(_f(ev).get("sim"))
        if v is None:
            continue
        per_track[ev.get("track_id")].append(v)

    out: Dict[str, Any] = {"n_tracks": 0, "window": window, "per_track": []}
    stds: List[float] = []
    for tid, sims in per_track.items():
        arr = np.asarray(sims, dtype=np.float64)
        if arr.size < 2:
            continue
        # Rolling std via convolution-style window.
        w = max(2, min(window, arr.size))
        roll = np.empty(arr.size - w + 1, dtype=np.float64)
        for i in range(roll.size):
            roll[i] = arr[i:i + w].std(ddof=0)
        out["per_track"].append({
            "track_id": tid,
            "n": int(arr.size),
            "sim_mean": float(arr.mean()),
            "sim_std": float(arr.std(ddof=0)),
            "rolling_std_p95": float(np.percentile(roll, 95)) if roll.size else 0.0,
        })
        stds.append(float(arr.std(ddof=0)))
    out["n_tracks"] = len(out["per_track"])
    out["sim_std_mean"] = float(np.mean(stds)) if stds else 0.0
    return out


# ── PAD ───────────────────────────────────────────────────────────────────────

def pad_temporal(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """PAD label fractions, overall + per track."""
    per_track: Dict[Any, Counter] = defaultdict(Counter)
    overall: Counter = Counter()
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        lbl = _f(ev).get("lbl")
        if not lbl:
            continue
        s = str(lbl)
        per_track[ev.get("track_id")][s] += 1
        overall[s] += 1

    total = sum(overall.values())
    out: Dict[str, Any] = {
        "n": total,
        "overall": {
            "real_fraction": overall["REAL"] / total if total else 0.0,
            "spoof_fraction": overall["SPOOF"] / total if total else 0.0,
            "uncertain_fraction": overall["UNCERTAIN"] / total if total else 0.0,
        },
        "per_track": [],
    }
    for tid, c in per_track.items():
        t = sum(c.values())
        out["per_track"].append({
            "track_id": tid,
            "n": t,
            "real_fraction": c["REAL"] / t if t else 0.0,
            "spoof_fraction": c["SPOOF"] / t if t else 0.0,
            "uncertain_fraction": c["UNCERTAIN"] / t if t else 0.0,
        })
    return out


# ── Thermal ───────────────────────────────────────────────────────────────────

def thermal_stats(
    events: Iterable[Dict[str, Any]],
    threshold_c: float = 75.0,
) -> Dict[str, Any]:
    """CPU temperature percentiles + over-threshold rate."""
    temps: List[float] = []
    for ev in events:
        v = _to_float(_f(ev).get("cpu_temp_c"))
        if v is None or v == 0.0:
            continue
        temps.append(v)
    arr = np.asarray(temps, dtype=np.float64)
    out: Dict[str, Any] = {"n": int(arr.size), "threshold_c": float(threshold_c)}
    if arr.size == 0:
        return out
    out.update(_percentile_block(arr))
    out["over_threshold_frames"] = int((arr >= threshold_c).sum())
    out["over_threshold_rate"] = float((arr >= threshold_c).mean())
    return out


# ── Bounding-box stability ────────────────────────────────────────────────────

def bbox_stability(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """face_w * face_h coefficient-of-variation per track."""
    per_track: Dict[Any, List[float]] = defaultdict(list)
    for ev in events:
        f = _f(ev)
        w = _to_float(f.get("face_w"))
        h = _to_float(f.get("face_h"))
        if w is None or h is None:
            continue
        per_track[ev.get("track_id")].append(w * h)
    out: Dict[str, Any] = {"n_tracks": 0, "per_track": []}
    cvs: List[float] = []
    for tid, areas in per_track.items():
        arr = np.asarray(areas, dtype=np.float64)
        if arr.size == 0:
            continue
        mean = float(arr.mean())
        std = float(arr.std(ddof=0)) if arr.size > 1 else 0.0
        cv = std / mean if mean > 0 else 0.0
        out["per_track"].append({
            "track_id": tid,
            "n": int(arr.size),
            "area_mean": mean,
            "area_std": std,
            "area_cv": cv,
        })
        cvs.append(cv)
    out["n_tracks"] = len(out["per_track"])
    out["area_cv_mean"] = float(np.mean(cvs)) if cvs else 0.0
    return out


# ── Bundled summary ──────────────────────────────────────────────────────────

def stabilization_summary(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """One-shot summary suitable for ``GET /api/metrics/stabilization``.

    Materialises the event iterator once so each metric reuses the same
    list (cheap for session-scoped event counts; the dashboard router
    enforces a 50k upper bound on the cross-experiment scope).
    """
    materialised = list(events)
    return {
        "n_events": len(materialised),
        "orientation": orientation_stability(materialised),
        "confidence": confidence_stability(materialised),
        "pad": pad_temporal(materialised),
        "thermal": thermal_stats(materialised),
        "bbox": bbox_stability(materialised),
    }


__all__ = [
    "orientation_stability",
    "confidence_stability",
    "pad_temporal",
    "thermal_stats",
    "bbox_stability",
    "stabilization_summary",
]
