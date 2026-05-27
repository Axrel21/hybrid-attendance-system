# cloud_backend/analytics/metrics.py
"""Stateless metric helpers consumed by the dashboard router.

All functions accept a list of event dicts (the same shape ingested by
:mod:`cloud_backend.telemetry.api`) and return JSON-serialisable
dictionaries. None of them raise on empty input — they return ``{"n": 0,
...}`` with sensible neutral values so the dashboard can render gaps as
"no data" rather than 500s.

Numpy is required (cheap import). Pandas is **not** imported here.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np


# ── Aggregations over the ingest event stream ─────────────────────────────────

def agreement_rate(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Edge/cloud agreement over events that carry ``edge_cloud_agree``.

    ``edge_cloud_agree`` is recorded per cloud offload (``edge.main``
    sets it from ``CloudVerificationResult.edge_cloud_agree``). It may be
    on the event row itself or nested under ``fields``.
    """
    agree = 0
    disagree = 0
    none = 0
    n = 0
    for ev in events:
        v = _extract(ev, "edge_cloud_agree")
        if v is None:
            none += 1
            continue
        n += 1
        if _truthy(v):
            agree += 1
        else:
            disagree += 1
    rate = (agree / n) if n else 0.0
    return {
        "n": n,
        "agree": agree,
        "disagree": disagree,
        "no_signal": none,
        "rate": rate,
    }


def offload_outcome_distribution(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Distribution of ``cloud_outcome`` (or ``offload_outcome``) values."""
    counter: Counter = Counter()
    n = 0
    for ev in events:
        v = _extract(ev, "cloud_outcome") or _extract(ev, "offload_outcome")
        if v is None:
            continue
        counter[str(v)] += 1
        n += 1
    total = sum(counter.values())
    distribution = {k: {"count": c, "fraction": c / total} for k, c in counter.items()} if total else {}
    return {
        "n": n,
        "distribution": distribution,
    }


def latency_summary(events: Iterable[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """Percentile summary over a numeric event field (e.g. ``cloud_rtt_ms``)."""
    samples: List[float] = []
    for ev in events:
        v = _extract(ev, key)
        f = _to_float(v)
        if f is None:
            continue
        samples.append(f)
    if not samples:
        return {"n": 0, "key": key}
    arr = np.asarray(samples, dtype=np.float64)
    return {
        "n": int(arr.size),
        "key": key,
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=0)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }


# ── ROC / FAR / FRR / EER  (ROC-readiness groundwork) ─────────────────────────

def far_frr(
    scores: np.ndarray,
    labels: np.ndarray,
    threshold: float,
) -> Dict[str, float]:
    """Compute FAR / FRR at a single threshold.

    Args:
        scores: float array of similarity scores.
        labels: int/bool array; 1 = genuine, 0 = impostor.
        threshold: accept if score >= threshold.
    """
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels).astype(bool)
    if scores.size == 0:
        return {"threshold": float(threshold), "far": 0.0, "frr": 0.0, "n_genuine": 0, "n_impostor": 0}
    accept = scores >= threshold
    genuine = labels
    impostor = ~labels
    n_genuine = int(genuine.sum())
    n_impostor = int(impostor.sum())
    frr = float((genuine & ~accept).sum() / n_genuine) if n_genuine else 0.0
    far = float((impostor & accept).sum() / n_impostor) if n_impostor else 0.0
    return {
        "threshold": float(threshold),
        "far": far,
        "frr": frr,
        "n_genuine": n_genuine,
        "n_impostor": n_impostor,
    }


def roc_curve(scores: np.ndarray, labels: np.ndarray) -> Dict[str, Any]:
    """Sweep over score thresholds and return (FAR, TAR, threshold) points.

    Returns the curve as parallel lists so the result is directly JSON
    serialisable. The sweep uses every unique score as a threshold.
    """
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels).astype(bool)
    if scores.size == 0:
        return {"n": 0, "thresholds": [], "far": [], "tar": [], "frr": []}
    thresholds = np.unique(np.concatenate([scores, np.array([scores.min() - 1e-6])]))
    thresholds.sort()
    far_list: List[float] = []
    tar_list: List[float] = []
    frr_list: List[float] = []
    n_genuine = int(labels.sum())
    n_impostor = int((~labels).sum())
    for th in thresholds:
        accept = scores >= th
        far = float(((accept) & (~labels)).sum() / n_impostor) if n_impostor else 0.0
        tar = float(((accept) & (labels)).sum() / n_genuine) if n_genuine else 0.0
        frr = 1.0 - tar
        far_list.append(far)
        tar_list.append(tar)
        frr_list.append(frr)
    return {
        "n": int(scores.size),
        "thresholds": [float(t) for t in thresholds],
        "far": far_list,
        "tar": tar_list,
        "frr": frr_list,
    }


def eer(scores: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
    """Equal error rate — threshold where FAR == FRR (interpolated)."""
    roc = roc_curve(scores, labels)
    if roc["n"] == 0:
        return {"eer": 0.0, "threshold": 0.0, "n": 0}
    far = np.asarray(roc["far"])
    frr = np.asarray(roc["frr"])
    diff = far - frr
    if diff.size < 2:
        return {"eer": float((far[0] + frr[0]) / 2), "threshold": float(roc["thresholds"][0]), "n": roc["n"]}
    # Find the sign-change index, interpolate.
    sign = np.sign(diff)
    crossings = np.where(np.diff(sign) != 0)[0]
    if crossings.size == 0:
        # No crossing — fall back to argmin |far - frr|.
        idx = int(np.argmin(np.abs(diff)))
        return {
            "eer": float((far[idx] + frr[idx]) / 2),
            "threshold": float(roc["thresholds"][idx]),
            "n": roc["n"],
        }
    i = int(crossings[0])
    # Linear interpolation between i and i+1.
    f0, f1 = far[i], far[i + 1]
    r0, r1 = frr[i], frr[i + 1]
    t0, t1 = roc["thresholds"][i], roc["thresholds"][i + 1]
    denom = (f1 - f0) - (r1 - r0)
    alpha = 0.0 if denom == 0 else (r0 - f0) / denom
    eer_val = float(f0 + alpha * (f1 - f0))
    th_val = float(t0 + alpha * (t1 - t0))
    return {"eer": eer_val, "threshold": th_val, "n": roc["n"]}


# ── Internals ─────────────────────────────────────────────────────────────────

def _extract(ev: Dict[str, Any], key: str) -> Any:
    """Pull ``key`` from either the top level or ``fields`` sub-dict."""
    if key in ev:
        return ev[key]
    fields = ev.get("fields")
    if isinstance(fields, dict):
        return fields.get(key)
    return None


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "t", "y")


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if np.isnan(f):
        return None
    return f
