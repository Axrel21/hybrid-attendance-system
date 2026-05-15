# cloud_backend/analytics/quality.py
"""Cloud-side quality-gate evaluator.

Mirror of :mod:`research.analysis.quality_gates` for the JSONL event
stream. Computes the same gate inputs from per-event ``fields`` and
returns a tag list with the same shape (``tag``, ``severity``, ``value``,
``threshold``, ``detail``).

Used by the dashboard to surface quality issues without re-running the
offline analyzer. Numpy only; ``{"n": 0, ...}`` on empty input.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from cloud_backend.analytics import stabilization as cs

try:
    from shared.contracts import QUALITY_GATE_DEFAULTS
except Exception:  # noqa: BLE001
    QUALITY_GATE_DEFAULTS = {}


# ── Helpers (mirror runtime_diagnostics.py minimally) ────────────────────────

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


def _proximity_close_fraction(events: List[Dict[str, Any]], min_distance_m: float = 0.4,
                               buffer_m: float = 0.5) -> Optional[float]:
    distances = []
    for ev in events:
        v = _to_float(_f(ev).get("distance"))
        if v is not None:
            distances.append(v)
    if not distances:
        return None
    arr = np.asarray(distances, dtype=np.float64)
    return float((arr < (min_distance_m + buffer_m)).mean())


def _brightness_p50(events: List[Dict[str, Any]]) -> Optional[float]:
    vals = []
    for ev in events:
        v = _to_float(_f(ev).get("brightness"))
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return float(np.median(vals))


def _blur_p50(events: List[Dict[str, Any]]) -> Optional[float]:
    vals = []
    for ev in events:
        v = _to_float(_f(ev).get("avg_blur"))
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return float(np.median(vals))


def _identity_max_distinct(events: List[Dict[str, Any]]) -> Optional[int]:
    per_track: Dict[Any, set] = defaultdict(set)
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        ident = _f(ev).get("identity")
        if ident and str(ident) != "NA":
            per_track[ev.get("track_id")].add(str(ident))
    if not per_track:
        return None
    return int(max(len(s) for s in per_track.values()))


def _pad_flip_rate(events: List[Dict[str, Any]]) -> Optional[float]:
    per_track: Dict[Any, List[str]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        lbl = _f(ev).get("lbl")
        if lbl:
            per_track[ev.get("track_id")].append(str(lbl))
    total_flips = 0
    total_transitions = 0
    for labels in per_track.values():
        for a, b in zip(labels, labels[1:]):
            total_transitions += 1
            if a != b:
                total_flips += 1
    if total_transitions == 0:
        return None
    return total_flips / total_transitions


def _offload_rate(events: List[Dict[str, Any]]) -> Optional[float]:
    n = 0
    off = 0
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        n += 1
        if _f(ev).get("decision") == "OFFLOAD_TO_CLOUD":
            off += 1
    return (off / n) if n else None


def _offload_failure_rate(events: List[Dict[str, Any]]) -> Optional[float]:
    counter: Counter = Counter()
    for ev in events:
        outcome = _f(ev).get("cloud_outcome")
        if outcome:
            counter[str(outcome)] += 1
    total = sum(counter.values())
    if total == 0:
        return None
    non_success = sum(v for k, v in counter.items() if k != "success")
    return non_success / total


def _sim_mean(events: List[Dict[str, Any]]) -> Optional[float]:
    vals = []
    for ev in events:
        v = _to_float(_f(ev).get("sim"))
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return float(np.mean(vals))


def _active_fraction_mean(events: List[Dict[str, Any]]) -> Optional[float]:
    per_track: Dict[Any, List[int]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") != "diagnostic":
            continue
        dec = _f(ev).get("decision")
        per_track[ev.get("track_id")].append(0 if dec == "NO_MATCH" else 1)
    if not per_track:
        return None
    fractions = [sum(v) / len(v) for v in per_track.values() if v]
    return float(np.mean(fractions)) if fractions else None


def _thermal_over_rate(events: List[Dict[str, Any]], threshold_c: float = 75.0) -> Optional[float]:
    vals = []
    for ev in events:
        v = _to_float(_f(ev).get("cpu_temp_c"))
        if v is not None and v > 0:
            vals.append(v)
    if not vals:
        return None
    arr = np.asarray(vals, dtype=np.float64)
    return float((arr >= threshold_c).mean())


# ── Tag builder (same shape as research.analysis.quality_gates) ──────────────

def _eval_pair(
    name: str,
    value: Optional[float],
    warn_th: Optional[float],
    alert_th: Optional[float],
    comparator: str,
    detail: str,
) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if comparator == "lt":
        if alert_th is not None and value < alert_th:
            return {"tag": name, "severity": "alert", "value": float(value),
                    "threshold": float(alert_th), "detail": detail}
        if warn_th is not None and value < warn_th:
            return {"tag": name, "severity": "warn", "value": float(value),
                    "threshold": float(warn_th), "detail": detail}
    elif comparator == "gt":
        if alert_th is not None and value > alert_th:
            return {"tag": name, "severity": "alert", "value": float(value),
                    "threshold": float(alert_th), "detail": detail}
        if warn_th is not None and value > warn_th:
            return {"tag": name, "severity": "warn", "value": float(value),
                    "threshold": float(warn_th), "detail": detail}
    return None


def evaluate(
    events: Iterable[Dict[str, Any]],
    overrides: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Return a tag list + severity counts from the event stream."""
    overrides = overrides or {}
    th = {**QUALITY_GATE_DEFAULTS, **overrides}
    materialised = list(events)

    bbox = cs.bbox_stability(materialised)
    orient = cs.orientation_stability(materialised)
    therm = cs.thermal_stats(materialised)

    tags: List[Dict[str, Any]] = []

    # unstable_camera
    t = _eval_pair(
        "unstable_camera", bbox.get("area_cv_mean"),
        th.get("bbox_area_cv_warn"), th.get("bbox_area_cv_alert"),
        "gt", "mean face-area coefficient of variation across tracks",
    )
    if t: tags.append(t)

    # low_light
    t = _eval_pair(
        "low_light", _brightness_p50(materialised),
        th.get("brightness_p50_warn"), th.get("brightness_p50_alert"),
        "lt", "median brightness across all events",
    )
    if t: tags.append(t)

    # excessive_blur
    t = _eval_pair(
        "excessive_blur", _blur_p50(materialised),
        th.get("blur_p50_warn"), th.get("blur_p50_alert"),
        "lt", "median Laplacian-variance (avg_blur) across all events",
    )
    if t: tags.append(t)

    # excessive_proximity
    t = _eval_pair(
        "excessive_proximity", _proximity_close_fraction(materialised),
        th.get("proximity_close_frac_warn"), th.get("proximity_close_frac_alert"),
        "gt", "fraction of frames within 0.5m of MIN_DISTANCE",
    )
    if t: tags.append(t)

    # unstable_tracking
    t = _eval_pair(
        "unstable_tracking", _active_fraction_mean(materialised),
        th.get("active_fraction_warn"), th.get("active_fraction_alert"),
        "lt", "mean active-frame fraction across tracks",
    )
    if t: tags.append(t)

    # thermal_warning
    t = _eval_pair(
        "thermal_warning", _thermal_over_rate(materialised),
        th.get("thermal_over_rate_warn"), th.get("thermal_over_rate_alert"),
        "gt", "fraction of frames over thermal threshold",
    )
    if t: tags.append(t)

    # low_confidence_run
    t = _eval_pair(
        "low_confidence_run", _sim_mean(materialised),
        th.get("sim_real_mean_warn"), th.get("sim_real_mean_alert"),
        "lt", "mean similarity across all events",
    )
    if t: tags.append(t)

    # frequent_spoof_flips
    t = _eval_pair(
        "frequent_spoof_flips", _pad_flip_rate(materialised),
        th.get("pad_flip_rate_warn"), th.get("pad_flip_rate_alert"),
        "gt", "PAD label flip rate (adjacent-frame transitions)",
    )
    if t: tags.append(t)

    # excessive_offload
    t = _eval_pair(
        "excessive_offload", _offload_rate(materialised),
        th.get("offload_rate_warn"), th.get("offload_rate_alert"),
        "gt", "fraction of diagnostic events triggering offload",
    )
    if t: tags.append(t)

    # identity_flicker
    md = _identity_max_distinct(materialised)
    t = _eval_pair(
        "identity_flicker", md,
        th.get("identity_distinct_warn"), th.get("identity_distinct_alert"),
        "gt", "maximum distinct identities within a single track",
    )
    if t: tags.append(t)

    # orientation_unstable
    t = _eval_pair(
        "orientation_unstable", orient.get("mode_flip_rate_mean"),
        th.get("mode_flip_rate_warn"), th.get("mode_flip_rate_alert"),
        "gt", "mean orientation-mode flip rate across tracks",
    )
    if t: tags.append(t)

    # high_offload_failure
    t = _eval_pair(
        "high_offload_failure", _offload_failure_rate(materialised),
        th.get("offload_failure_rate_warn"), th.get("offload_failure_rate_alert"),
        "gt", "fraction of cloud_outcome != 'success' among offload attempts",
    )
    if t: tags.append(t)

    return {
        "n_events": len(materialised),
        "tags": tags,
        "tag_count": len(tags),
        "by_severity": {
            sev: sum(1 for t in tags if t["severity"] == sev)
            for sev in ("info", "warn", "alert")
        },
    }


__all__ = ["evaluate"]
