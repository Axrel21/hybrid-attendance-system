# research/analysis/stability_score.py
"""Composite 0..1 stability score from existing summary fields.

Combines a handful of normalised signals into a single scalar so the
operator can sort or filter sessions quickly. The score is **descriptive,
not prescriptive** — high score means "looks stable on the existing
diagnostics", not "fit for production".

Each signal is normalised into ``[0, 1]`` then averaged with the weights
from :data:`shared.contracts.STABILITY_SCORE_WEIGHTS`. Missing signals
fall out of the average (the score is rescaled accordingly).

The contributions dict makes it auditable: ``components["sim_std_inv"] =
0.18`` means that signal contributed 0.18 to the final score.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from shared.contracts import STABILITY_SCORE_WEIGHTS
except Exception:  # noqa: BLE001
    STABILITY_SCORE_WEIGHTS = {
        "active_fraction": 0.25,
        "mode_flip_rate_inv": 0.15,
        "sim_std_inv": 0.20,
        "pad_flip_rate_inv": 0.15,
        "identity_distinct_inv": 0.10,
        "thermal_safe_rate": 0.05,
        "offload_success_rate": 0.10,
    }


log = logging.getLogger("research.analysis.stability_score")


def _g(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _clip01(x: float) -> float:
    if x < 0:
        return 0.0
    if x > 1:
        return 1.0
    return x


def _signal_active_fraction(report: Dict[str, Any]) -> Optional[float]:
    v = _g(report, "stabilization.detection_persistence.mean_active_fraction")
    return _clip01(float(v)) if v is not None else None


def _signal_mode_flip_inv(report: Dict[str, Any]) -> Optional[float]:
    v = _g(report, "stabilization.orientation_stability.mode_flip_rate_mean")
    return _clip01(1.0 - float(v)) if v is not None else None


def _signal_sim_std_inv(report: Dict[str, Any]) -> Optional[float]:
    v = _g(report, "stabilization.confidence_stability.sim_std_mean")
    if v is None:
        return None
    # sim_std in practice runs 0.0 (tight) .. 0.3+ (noisy). Map [0, 0.30] -> [1, 0].
    return _clip01(1.0 - (float(v) / 0.30))


def _signal_pad_flip_inv(report: Dict[str, Any]) -> Optional[float]:
    v = _g(report, "runtime_diagnostics.pad_hysteresis.overall_flip_rate")
    return _clip01(1.0 - float(v)) if v is not None else None


def _signal_identity_distinct_inv(report: Dict[str, Any]) -> Optional[float]:
    v = _g(report, "runtime_diagnostics.identity_flicker.max_distinct")
    if v is None:
        return None
    # 1 distinct identity per track -> 1.0; 2 -> 0.5; 4 -> 0.25; etc.
    return _clip01(1.0 / max(1.0, float(v)))


def _signal_thermal_safe_rate(report: Dict[str, Any]) -> Optional[float]:
    v = _g(report, "stabilization.thermal.over_threshold_rate")
    return _clip01(1.0 - float(v)) if v is not None else None


def _signal_offload_success_rate(report: Dict[str, Any]) -> Optional[float]:
    counts = _g(report, "stabilization.offload_trigger.outcome_counts") or {}
    total = sum(counts.values()) if isinstance(counts, dict) else 0
    if total == 0:
        # No offloads attempted — assume neutral. Treat as missing rather
        # than 1.0 so the score doesn't reward never-offloading.
        return None
    return _clip01(float(counts.get("success", 0)) / total)


_SIGNAL_FUNCS = {
    "active_fraction": _signal_active_fraction,
    "mode_flip_rate_inv": _signal_mode_flip_inv,
    "sim_std_inv": _signal_sim_std_inv,
    "pad_flip_rate_inv": _signal_pad_flip_inv,
    "identity_distinct_inv": _signal_identity_distinct_inv,
    "thermal_safe_rate": _signal_thermal_safe_rate,
    "offload_success_rate": _signal_offload_success_rate,
}


def compute_score(
    report: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    weights = weights or STABILITY_SCORE_WEIGHTS
    components: Dict[str, Dict[str, float]] = {}
    missing: List[str] = []
    weighted_sum = 0.0
    weight_total = 0.0
    for name, w in weights.items():
        fn = _SIGNAL_FUNCS.get(name)
        if fn is None:
            missing.append(name)
            continue
        value = fn(report)
        if value is None:
            missing.append(name)
            continue
        contribution = float(value) * float(w)
        components[name] = {
            "value": float(value),
            "weight": float(w),
            "contribution": contribution,
        }
        weighted_sum += contribution
        weight_total += float(w)

    score = (weighted_sum / weight_total) if weight_total > 0 else 0.0
    return {
        "session_id": report.get("session_id"),
        "score": _clip01(score),
        "components": components,
        "missing_signals": missing,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument("--out", default=None,
                   help="output JSON path (default: <session>/summaries/stability_score.json)")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    session_dir = Path(args.session)
    report_path = session_dir / "summaries" / "stabilization_report.json"
    if not report_path.exists():
        from research.analysis.stabilization_report import build_report
        report = build_report(session_dir)
    else:
        with open(report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)

    payload = compute_score(report)
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "stability_score.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info("score=%.3f, components=%d, missing=%s",
             payload["score"], len(payload["components"]), payload["missing_signals"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
