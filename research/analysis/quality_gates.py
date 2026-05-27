# research/analysis/quality_gates.py
"""Soft quality-gate evaluator.

Reads the offline diagnostic summaries produced by
:mod:`research.analysis.stabilization` and
:mod:`research.analysis.runtime_diagnostics` and emits a list of
quality tags. Tags are **soft** — they surface observability concerns,
they never reject a run.

Tag vocabulary lives in :mod:`shared.contracts.QUALITY_TAGS`. Default
thresholds in :mod:`shared.contracts.QUALITY_GATE_DEFAULTS`. Operators
override per-tag thresholds with ``--threshold KEY=VALUE`` on the CLI.

CLI examples
------------
::

    python -m research.analysis.quality_gates \\
        --session experiments/exp_20260516_120000

    # Override the brightness alert threshold for outdoor sessions
    python -m research.analysis.quality_gates \\
        --session experiments/exp_20260516_120000 \\
        --threshold brightness_p50_alert=20.0
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from research.analysis.runtime_diagnostics import diagnose_session
from research.analysis.stabilization import summarize_session

try:
    from shared.contracts import QUALITY_GATE_DEFAULTS, QUALITY_TAGS
except Exception:  # noqa: BLE001
    QUALITY_GATE_DEFAULTS = {}
    QUALITY_TAGS = ()


log = logging.getLogger("research.analysis.quality_gates")


# ── Threshold container ──────────────────────────────────────────────────────

@dataclass
class GateThresholds:
    """Per-gate thresholds. Default values from :mod:`shared.contracts`."""

    overrides: Dict[str, float] = field(default_factory=dict)

    def get(self, key: str) -> Optional[float]:
        if key in self.overrides:
            return self.overrides[key]
        return QUALITY_GATE_DEFAULTS.get(key)


# ── Tag builders ─────────────────────────────────────────────────────────────

def _tag(name: str, severity: str, value: float, threshold: float, detail: str) -> Dict[str, Any]:
    return {
        "tag": name,
        "severity": severity,
        "value": float(value) if value is not None else None,
        "threshold": float(threshold) if threshold is not None else None,
        "detail": detail,
    }


def _eval_pair(
    name: str,
    value: Optional[float],
    warn_th: Optional[float],
    alert_th: Optional[float],
    comparator: str,
    detail: str,
) -> Optional[Dict[str, Any]]:
    """Compare ``value`` against (warn, alert) thresholds and return the most-severe tag."""
    if value is None:
        return None
    # Comparator semantics:
    #   "lt" => alert if value < alert_th, warn if value < warn_th  (smaller is worse)
    #   "gt" => alert if value > alert_th, warn if value > warn_th  (larger is worse)
    if comparator == "lt":
        if alert_th is not None and value < alert_th:
            return _tag(name, "alert", value, alert_th, detail)
        if warn_th is not None and value < warn_th:
            return _tag(name, "warn", value, warn_th, detail)
    elif comparator == "gt":
        if alert_th is not None and value > alert_th:
            return _tag(name, "alert", value, alert_th, detail)
        if warn_th is not None and value > warn_th:
            return _tag(name, "warn", value, warn_th, detail)
    return None


# ── Aggregator ───────────────────────────────────────────────────────────────

def evaluate_metrics(
    stabilization: Dict[str, Any],
    runtime: Dict[str, Any],
    thresholds: Optional[GateThresholds] = None,
) -> List[Dict[str, Any]]:
    """Produce tag list from already-computed metric dicts."""
    th = thresholds or GateThresholds()
    tags: List[Dict[str, Any]] = []

    # unstable_camera — bbox area CV
    val = stabilization.get("bbox_stability", {}).get("area_cv_mean")
    t = _eval_pair(
        "unstable_camera", val,
        th.get("bbox_area_cv_warn"), th.get("bbox_area_cv_alert"),
        "gt", "mean face-area coefficient of variation across tracks",
    )
    if t: tags.append(t)

    # excessive_blur — blur p50 (smaller = blurrier)
    blur_p50 = (stabilization.get("blur_geometry_quality", {}) or {}).get("blur", {}).get("p50")
    t = _eval_pair(
        "excessive_blur", blur_p50,
        th.get("blur_p50_warn"), th.get("blur_p50_alert"),
        "lt", "median Laplacian-variance over the session",
    )
    if t: tags.append(t)

    # low_light — brightness p50; the diagnostic CSV exposes brightness in the
    # blur_geometry_quality block isn't ideal — we add a dedicated check using
    # the runtime block when present.
    bp = stabilization.get("blur_geometry_quality", {}).get("face_area", {})
    # brightness lives in the raw CSV; use stabilization 'distance_m' as proxy only if needed.
    # We pull brightness percentile from runtime distance_vs_confidence section if present.
    # Best route: read directly from diagnostics in evaluate_session.

    # excessive_proximity — close_fraction in runtime.proximity
    close_frac = runtime.get("proximity", {}).get("close_fraction")
    t = _eval_pair(
        "excessive_proximity", close_frac,
        th.get("proximity_close_frac_warn"), th.get("proximity_close_frac_alert"),
        "gt", "fraction of frames within 0.5m of MIN_DISTANCE",
    )
    if t: tags.append(t)

    # unstable_tracking — detection_persistence.mean_active_fraction
    af = stabilization.get("detection_persistence", {}).get("mean_active_fraction")
    t = _eval_pair(
        "unstable_tracking", af,
        th.get("active_fraction_warn"), th.get("active_fraction_alert"),
        "lt", "mean active-frame fraction across tracks",
    )
    if t: tags.append(t)

    # thermal_warning — thermal.over_threshold_rate
    otr = stabilization.get("thermal", {}).get("over_threshold_rate")
    t = _eval_pair(
        "thermal_warning", otr,
        th.get("thermal_over_rate_warn"), th.get("thermal_over_rate_alert"),
        "gt", "fraction of frames over thermal threshold",
    )
    if t: tags.append(t)

    # low_confidence_run — mean sim across REAL frames
    track_summary = runtime.get("track_recognition", {}).get("per_track", [])
    if track_summary:
        sim_mean_overall = float(np.mean([t_["sim_mean"] for t_ in track_summary]))
        t = _eval_pair(
            "low_confidence_run", sim_mean_overall,
            th.get("sim_real_mean_warn"), th.get("sim_real_mean_alert"),
            "lt", "mean similarity across all tracks",
        )
        if t: tags.append(t)

    # frequent_spoof_flips — pad_hysteresis.overall_flip_rate
    fr = runtime.get("pad_hysteresis", {}).get("overall_flip_rate")
    t = _eval_pair(
        "frequent_spoof_flips", fr,
        th.get("pad_flip_rate_warn"), th.get("pad_flip_rate_alert"),
        "gt", "PAD label flip rate (adjacent-frame transitions)",
    )
    if t: tags.append(t)

    # excessive_offload — stabilization.offload_trigger.offload_trigger_rate
    o = stabilization.get("offload_trigger", {}).get("offload_trigger_rate")
    t = _eval_pair(
        "excessive_offload", o,
        th.get("offload_rate_warn"), th.get("offload_rate_alert"),
        "gt", "fraction of frames triggering cloud offload",
    )
    if t: tags.append(t)

    # identity_flicker — max distinct identities per track
    max_distinct = runtime.get("identity_flicker", {}).get("max_distinct")
    t = _eval_pair(
        "identity_flicker", max_distinct,
        th.get("identity_distinct_warn"), th.get("identity_distinct_alert"),
        "gt", "maximum distinct identities observed within a single track",
    )
    if t: tags.append(t)

    # orientation_unstable — orientation.mode_flip_rate_mean
    mfr = stabilization.get("orientation_stability", {}).get("mode_flip_rate_mean")
    t = _eval_pair(
        "orientation_unstable", mfr,
        th.get("mode_flip_rate_warn"), th.get("mode_flip_rate_alert"),
        "gt", "mean orientation-mode flip rate across tracks",
    )
    if t: tags.append(t)

    # high_offload_failure — non-success rate within outcome_counts
    counts = stabilization.get("offload_trigger", {}).get("outcome_counts") or {}
    total = sum(counts.values()) if counts else 0
    if total > 0:
        non_success = sum(v for k, v in counts.items() if k != "success")
        rate = non_success / total
        t = _eval_pair(
            "high_offload_failure", rate,
            th.get("offload_failure_rate_warn"), th.get("offload_failure_rate_alert"),
            "gt", "fraction of cloud_outcome != 'success' among offload attempts",
        )
        if t: tags.append(t)

    return tags


def evaluate_session(
    session_dir: Path,
    thresholds: Optional[GateThresholds] = None,
    write_to: Optional[Path] = None,
) -> Dict[str, Any]:
    diag = Path(session_dir) / "diagnostics" / "diagnostic_log.csv"
    if not diag.exists():
        raise FileNotFoundError(f"diagnostic CSV not found: {diag}")

    stabilization = summarize_session(diag)
    runtime = diagnose_session(diag)

    # ``low_light`` is computed here because the inputs aren't in either
    # summary block. Read brightness directly from the CSV.
    import pandas as pd
    df = pd.read_csv(diag)
    brightness_p50 = None
    if "brightness" in df.columns:
        b = pd.to_numeric(df["brightness"], errors="coerce").dropna()
        if len(b) > 0:
            brightness_p50 = float(b.median())

    th = thresholds or GateThresholds()
    tags = evaluate_metrics(stabilization, runtime, thresholds=th)

    if brightness_p50 is not None:
        t = _eval_pair(
            "low_light", brightness_p50,
            th.get("brightness_p50_warn"), th.get("brightness_p50_alert"),
            "lt", "median brightness across all frames",
        )
        if t:
            tags.append(t)

    payload = {
        "session_id": Path(session_dir).resolve().name,
        "diagnostic_csv": str(diag),
        "rows": int(len(df)),
        "tags": tags,
        "tag_count": len(tags),
        "by_severity": {
            sev: sum(1 for t in tags if t["severity"] == sev)
            for sev in ("info", "warn", "alert")
        },
        "thresholds": {k: th.get(k) for k in (QUALITY_GATE_DEFAULTS.keys() if QUALITY_GATE_DEFAULTS else [])},
    }

    if write_to is not None:
        write_to.parent.mkdir(parents=True, exist_ok=True)
        with open(write_to, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    return payload


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_overrides(raw: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for item in raw or []:
        if "=" not in item:
            raise ValueError(f"--threshold expects KEY=VALUE; got {item!r}")
        k, v = item.split("=", 1)
        try:
            out[k.strip()] = float(v.strip())
        except ValueError as exc:
            raise ValueError(f"--threshold value must be numeric: {item!r}") from exc
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument("--out", default=None,
                   help="output JSON path (default: <session>/summaries/quality_tags.json)")
    p.add_argument("--threshold", action="append", default=[],
                   metavar="KEY=VALUE",
                   help="override a gate threshold (e.g. --threshold brightness_p50_alert=20.0)")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    overrides = _parse_overrides(args.threshold)
    thresholds = GateThresholds(overrides=overrides)
    session_dir = Path(args.session)
    out_path = Path(args.out) if args.out else session_dir / "summaries" / "quality_tags.json"

    result = evaluate_session(session_dir, thresholds=thresholds, write_to=out_path)
    log.info(
        "wrote %s (tags=%d, alert=%d, warn=%d)",
        out_path, result["tag_count"],
        result["by_severity"].get("alert", 0),
        result["by_severity"].get("warn", 0),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
