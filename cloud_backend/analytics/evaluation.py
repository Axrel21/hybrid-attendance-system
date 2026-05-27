# cloud_backend/analytics/evaluation.py
"""Research-grade evaluation wrappers over the cloud event stream.

Composes existing helpers in :mod:`cloud_backend.analytics.metrics` /
:mod:`stabilization` / :mod:`calibration` / :mod:`quality` into shapes
that are easier to surface in research write-ups:

* :func:`pad_confusion_matrix` — given an attack-type label on the
  session, count REAL/SPOOF/UNCERTAIN frames and shape them as a 3×3 confusion-style table.
* :func:`orientation_robustness` — per-mode sim summary + overall
  robustness score.
* :func:`thermal_performance_tradeoff` — temperature × FPS scatter
  summary.
* :func:`offload_efficiency` — successful offloads / total offloads + RTT percentile.
* :func:`latency_distribution_comparison` — line up cloud RTT and edge
  per-stage latency.

Each function returns ``{"n": 0, ...}`` on empty input.
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


# ── PAD confusion-style table ────────────────────────────────────────────────

def pad_confusion_matrix(
    events: Iterable[Dict[str, Any]],
    attack_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Count REAL/SPOOF/UNCERTAIN frames per attack-type label.

    If ``attack_type`` is provided, the entire input is assumed to come
    from one session and the result is a single confusion row. Otherwise
    rows are grouped by ``event['protocol']['attack_type']`` if that
    field is present on each event (rare today; the cloud-side ingest
    doesn't push protocol per event, so the per-session call is the
    common path).
    """
    events = list(events)
    if not events:
        return {"n": 0, "rows": []}

    by_attack: Dict[str, Counter] = defaultdict(Counter)
    if attack_type is not None:
        bucket = attack_type
    else:
        bucket = "unknown"

    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        lbl = _f(ev).get("lbl")
        if not lbl:
            continue
        by_attack[bucket][str(lbl)] += 1

    rows = []
    for atk, counts in by_attack.items():
        total = sum(counts.values())
        rows.append({
            "attack_type": atk,
            "n": total,
            "real": int(counts.get("REAL", 0)),
            "spoof": int(counts.get("SPOOF", 0)),
            "uncertain": int(counts.get("UNCERTAIN", 0)),
            "real_fraction": counts.get("REAL", 0) / total if total else 0.0,
            "spoof_fraction": counts.get("SPOOF", 0) / total if total else 0.0,
            "uncertain_fraction": counts.get("UNCERTAIN", 0) / total if total else 0.0,
        })
    return {"n": sum(r["n"] for r in rows), "rows": rows}


# ── Orientation robustness ───────────────────────────────────────────────────

def orientation_robustness(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Sim mean / std per mode + a simple robustness score (1 - spread/range).

    Robustness score: ``1 - (max - min) / max(max, eps)`` over per-mode means.
    Higher = recognition is consistent across orientation.
    """
    per_mode: Dict[str, List[float]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        f = _f(ev)
        mode = f.get("mode") or f.get("mode_raw")
        sim = _to_float(f.get("sim"))
        if mode and sim is not None:
            per_mode[str(mode)].append(sim)

    rows = []
    means = []
    for mode, sims in per_mode.items():
        if not sims:
            continue
        arr = np.asarray(sims, dtype=np.float64)
        m = float(arr.mean())
        rows.append({
            "mode": mode,
            "n": int(arr.size),
            "sim_mean": m,
            "sim_std": float(arr.std(ddof=0)),
            "sim_p95": float(np.percentile(arr, 95)),
        })
        means.append(m)

    if means:
        hi, lo = max(means), min(means)
        score = 1.0 - (hi - lo) / max(hi, 1e-9)
    else:
        score = 0.0
    return {
        "n_modes": len(rows),
        "rows": rows,
        "robustness_score": float(max(0.0, min(1.0, score))),
    }


# ── Thermal × performance tradeoff ───────────────────────────────────────────

def thermal_performance_tradeoff(
    events: Iterable[Dict[str, Any]],
    threshold_c: float = 75.0,
) -> Dict[str, Any]:
    """Correlate cpu_temp_c with fps_rolling, plus over-threshold rate."""
    temps: List[float] = []
    fps: List[float] = []
    for ev in events:
        f = _f(ev)
        t = _to_float(f.get("cpu_temp_c"))
        s = _to_float(f.get("fps_rolling"))
        if t is None or t == 0 or s is None:
            continue
        temps.append(t)
        fps.append(s)
    if not temps:
        return {"n": 0}
    t_arr = np.asarray(temps, dtype=np.float64)
    f_arr = np.asarray(fps, dtype=np.float64)
    corr = float(np.corrcoef(t_arr, f_arr)[0, 1]) if t_arr.size > 1 else 0.0
    return {
        "n": int(t_arr.size),
        "threshold_c": float(threshold_c),
        "thermal_p95": float(np.percentile(t_arr, 95)),
        "fps_mean": float(f_arr.mean()),
        "thermal_fps_correlation": corr,
        "over_threshold_rate": float((t_arr >= threshold_c).mean()),
    }


# ── Offload efficiency ───────────────────────────────────────────────────────

def offload_efficiency(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Successful offloads / total offloads, plus agreement and RTT percentiles."""
    outcome_counts: Counter = Counter()
    rtts: List[float] = []
    agree = 0
    agree_total = 0
    for ev in events:
        f = _f(ev)
        outcome = f.get("cloud_outcome")
        if outcome is not None and outcome != "":
            outcome_counts[str(outcome)] += 1
        rtt = _to_float(f.get("cloud_rtt_ms"))
        if rtt is not None:
            rtts.append(rtt)
        agree_v = f.get("edge_cloud_agree")
        if agree_v not in (None, ""):
            agree_total += 1
            if str(agree_v).strip().lower() in ("true", "1", "yes"):
                agree += 1
    total = sum(outcome_counts.values())
    successes = outcome_counts.get("success", 0)
    payload: Dict[str, Any] = {
        "n_offloads": total,
        "success_count": int(successes),
        "success_rate": successes / total if total else 0.0,
        "outcome_counts": {k: int(v) for k, v in outcome_counts.items()},
        "agreement_rate": (agree / agree_total) if agree_total else 0.0,
        "agreement_n": int(agree_total),
    }
    if rtts:
        arr = np.asarray(rtts, dtype=np.float64)
        payload["rtt_ms"] = {
            "n": int(arr.size),
            "mean": float(arr.mean()),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
        }
    return payload


# ── Latency distribution comparison ──────────────────────────────────────────

def latency_distribution_comparison(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Percentile blocks for every per-stage timing field."""
    keys = (
        "t_capture_ms", "t_detect_ms", "t_tracks_ms",
        "t_liveness_ms", "t_embed_ms", "t_match_ms",
        "t_overlay_ms", "t_post_ms", "t_total_ms",
        "cloud_rtt_ms", "jpeg_encode_ms",
    )
    out: Dict[str, Any] = {"keys": list(keys), "rows": []}
    for k in keys:
        vals: List[float] = []
        for ev in events:
            v = _to_float(_f(ev).get(k))
            if v is not None:
                vals.append(v)
        if not vals:
            out["rows"].append({"key": k, "n": 0})
            continue
        arr = np.asarray(vals, dtype=np.float64)
        out["rows"].append({
            "key": k,
            "n": int(arr.size),
            "mean": float(arr.mean()),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
        })
    return out


__all__ = [
    "pad_confusion_matrix",
    "orientation_robustness",
    "thermal_performance_tradeoff",
    "offload_efficiency",
    "latency_distribution_comparison",
]
