# research/analysis/calibration.py
"""Human-readable calibration helpers.

This module is intentionally **decision-supporting, not self-adjusting**.
Every helper returns numbers + diagnostics so an operator can pick a
calibration point manually; nothing here writes back into the runtime
config.

Helpers
-------
* :func:`compare_confidence_distributions` — side-by-side percentile
  blocks for ``sim`` across N sessions.
* :func:`recommend_match_thresholds` — given a session's threshold sweep,
  suggest ``th_high`` candidates that hit a target matched / offload
  rate.
* :func:`recommend_hysteresis_gap` — given the per-track flip rates,
  suggest a wider mid/high gap to damp churn.
* :func:`recommend_routing_policy` — given multiple sessions captured
  under different ``CLOUD_ROUTING`` strategies, surface the one that
  maximises agreement rate while keeping offload-rate within bounds.
* :func:`pad_threshold_compare` — line up PAD label-fraction columns
  across sessions for an attack-type sweep.
* :func:`operating_point_snapshot` — capture the active thresholds /
  observed outcomes for a single session so calibration runs can be
  versioned and replayed later.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional


log = logging.getLogger("research.analysis.calibration")


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


def _load_report(session_dir: Path) -> Dict[str, Any]:
    rp = session_dir / "summaries" / "stabilization_report.json"
    if rp.exists():
        with open(rp, "r", encoding="utf-8") as fh:
            return json.load(fh)
    from research.analysis.stabilization_report import build_report
    return build_report(session_dir)


# ── Confidence-distribution comparison ───────────────────────────────────────

def compare_confidence_distributions(
    session_dirs: List[Path],
    key: str = "sim",
) -> Dict[str, Any]:
    """Project one numeric ``key`` distribution from each session's threshold
    sweep block (already computed by ``threshold_sweep``) for side-by-side
    inspection.
    """
    rows: List[Dict[str, Any]] = []
    for sdir in session_dirs:
        sdir = Path(sdir)
        try:
            report = _load_report(sdir)
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping %s: %s", sdir, exc)
            continue
        if key == "sim":
            block = _g(report, "threshold_sweep.sim_distribution")
        elif key == "live_conf":
            block = _g(report, "threshold_sweep.live_conf_distribution")
        else:
            block = None
        if block is None:
            continue
        rows.append({
            "session_id": report.get("session_id") or sdir.name,
            "key": key,
            **{k: block.get(k) for k in ("n", "mean", "std", "p50", "p90", "p95", "p99", "min", "max")},
        })
    return {"key": key, "session_count": len(rows), "rows": rows}


# ── Threshold recommendation ──────────────────────────────────────────────────

def recommend_match_thresholds(
    session_dir: Path,
    target_matched_rate: Optional[float] = None,
    target_offload_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Given a session's ``threshold_sweep`` block, surface ``th_high`` candidates
    closest to the operator's target.
    """
    report = _load_report(Path(session_dir))
    sweep = _g(report, "threshold_sweep.match_threshold_sweep") or []
    if not sweep:
        return {"recommendation": None, "detail": "no threshold sweep present"}

    candidates: List[Dict[str, Any]] = []
    for p in sweep:
        candidates.append({
            "th_high": p["th_high"],
            "th_mid": p.get("th_mid"),
            "matched_rate": p.get("matched_rate"),
            "offload_rate": p.get("offload_rate"),
            "below_threshold_rate": p.get("below_threshold_rate"),
        })

    chosen: Optional[Dict[str, Any]] = None
    detail = ""
    if target_matched_rate is not None:
        chosen = min(
            candidates,
            key=lambda c: abs((c["matched_rate"] or 0.0) - target_matched_rate),
        )
        detail = f"closest matched_rate to target={target_matched_rate}"
    elif target_offload_rate is not None:
        chosen = min(
            candidates,
            key=lambda c: abs((c["offload_rate"] or 0.0) - target_offload_rate),
        )
        detail = f"closest offload_rate to target={target_offload_rate}"
    else:
        # No target — pick the threshold maximising matched - offload.
        chosen = max(
            candidates,
            key=lambda c: ((c["matched_rate"] or 0.0) - (c["offload_rate"] or 0.0)),
        )
        detail = "no target supplied; chose max(matched_rate - offload_rate)"

    return {
        "session_id": report.get("session_id"),
        "recommendation": chosen,
        "candidates": candidates,
        "detail": detail,
    }


# ── Hysteresis-gap recommendation ────────────────────────────────────────────

def recommend_hysteresis_gap(
    session_dir: Path,
    target_max_flip_rate: float = 0.10,
) -> Dict[str, Any]:
    """Suggest a wider th_high/th_mid gap when adjacent-frame flips are too
    common. Decision-supporting only — never auto-applies.
    """
    report = _load_report(Path(session_dir))
    flip = _g(report, "threshold_sweep.hysteresis.overall_flip_rate")
    if flip is None:
        return {"recommendation": None, "detail": "no hysteresis block present"}
    current_gap = _g(report, "threshold_sweep.match_threshold_sweep") or []
    current_mid_offset = None
    if current_gap:
        first = current_gap[0]
        current_mid_offset = float(first["th_high"]) - float(first.get("th_mid") or 0)
    proposed_gap = current_mid_offset
    if flip > target_max_flip_rate and current_mid_offset is not None:
        # Linear bump: widen the gap proportionally to the flip-rate excess.
        excess = (flip - target_max_flip_rate) / max(target_max_flip_rate, 1e-6)
        proposed_gap = current_mid_offset * (1.0 + min(1.0, excess))
    return {
        "session_id": report.get("session_id"),
        "current_flip_rate": flip,
        "target_max_flip_rate": target_max_flip_rate,
        "current_mid_offset": current_mid_offset,
        "proposed_mid_offset": proposed_gap,
        "detail": "operator should re-run threshold_sweep with --mid-offset adjusted",
    }


# ── Routing-policy comparison ────────────────────────────────────────────────

def recommend_routing_policy(
    session_dirs: List[Path],
    max_offload_rate: float = 0.30,
) -> Dict[str, Any]:
    """Compare hybrid-routing sessions and recommend the strategy that
    maximises edge_cloud agreement while staying under ``max_offload_rate``.

    Reads ``protocol.environment.CLOUD_ROUTING`` (set by the operator
    during the routing sweep) to label each session.
    """
    rows: List[Dict[str, Any]] = []
    for sdir in session_dirs:
        sdir = Path(sdir)
        try:
            report = _load_report(sdir)
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping %s: %s", sdir, exc)
            continue
        env = _g(report, "protocol.environment") or {}
        strategy = env.get("CLOUD_ROUTING") if isinstance(env, dict) else None
        offload_rate = _g(report, "stabilization.offload_trigger.offload_trigger_rate")
        agreement = _g(report, "stabilization.offload_trigger.agreement_rate")
        rows.append({
            "session_id": report.get("session_id"),
            "strategy": strategy,
            "offload_rate": offload_rate,
            "agreement_rate": agreement,
        })

    eligible = [
        r for r in rows
        if isinstance(r["offload_rate"], (int, float))
        and r["offload_rate"] <= max_offload_rate
        and isinstance(r["agreement_rate"], (int, float))
    ]
    chosen = None
    if eligible:
        chosen = max(eligible, key=lambda r: r["agreement_rate"])

    return {
        "rows": rows,
        "max_offload_rate": max_offload_rate,
        "recommendation": chosen,
        "detail": (
            f"selected strategy={chosen['strategy']!r} (agreement={chosen['agreement_rate']:.3f})"
            if chosen
            else "no session met the offload-rate ceiling; widen --max-offload-rate or capture more data"
        ),
    }


# ── PAD threshold comparison ─────────────────────────────────────────────────

def pad_threshold_compare(session_dirs: List[Path]) -> Dict[str, Any]:
    """Line up per-session PAD label fractions by ``protocol.attack_type``."""
    rows: List[Dict[str, Any]] = []
    for sdir in session_dirs:
        try:
            report = _load_report(Path(sdir))
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping %s: %s", sdir, exc)
            continue
        proto = report.get("protocol") or {}
        attack = proto.get("attack_type")
        overall = _g(report, "stabilization.pad_temporal.overall") or {}
        rows.append({
            "session_id": report.get("session_id"),
            "attack_type": attack,
            "real_fraction": overall.get("real_fraction"),
            "spoof_fraction": overall.get("spoof_fraction"),
            "uncertain_fraction": overall.get("uncertain_fraction"),
        })
    return {"session_count": len(rows), "rows": rows}


# ── Operating-point snapshot ─────────────────────────────────────────────────

def operating_point_snapshot(session_dir: Path) -> Dict[str, Any]:
    """Capture the active thresholds + observed outcomes for one session.

    A calibration write-up should attach this snapshot so future readers
    can replay the conditions under which a threshold change was made.
    """
    report = _load_report(Path(session_dir))
    proto = report.get("protocol") or {}
    return {
        "session_id": report.get("session_id"),
        "snapshot_taken_at": (
            None  # caller sets ``recorded_at`` upstream if needed
        ),
        "protocol_summary": {
            "experiment_label": proto.get("experiment_label"),
            "attack_type": proto.get("attack_type"),
            "distance_m": proto.get("distance_m"),
            "lighting": proto.get("lighting"),
            "orientation": proto.get("orientation"),
        },
        "thresholds": _g(report, "stabilization.diagnostic_csv") and {},  # placeholder
        "observed": {
            "matched_rate_threshold_sweep_first": _g(
                report, "threshold_sweep.match_threshold_sweep.0.matched_rate",
            ),
            "active_fraction_mean": _g(
                report, "stabilization.detection_persistence.mean_active_fraction",
            ),
            "offload_trigger_rate": _g(
                report, "stabilization.offload_trigger.offload_trigger_rate",
            ),
            "agreement_rate": _g(
                report, "stabilization.offload_trigger.agreement_rate",
            ),
            "pad_real_fraction": _g(
                report, "stabilization.pad_temporal.overall.real_fraction",
            ),
            "thermal_p95": _g(report, "stabilization.thermal.p95"),
        },
        "quality_tags": (report.get("quality_tags") or {}).get("tags") or [],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ccd = sub.add_parser("compare-distributions")
    ccd.add_argument("--sessions", nargs="+", required=True)
    ccd.add_argument("--key", default="sim", choices=("sim", "live_conf"))
    ccd.add_argument("--out", required=True)

    rmt = sub.add_parser("recommend-thresholds")
    rmt.add_argument("--session", required=True)
    rmt.add_argument("--target-matched-rate", type=float, default=None)
    rmt.add_argument("--target-offload-rate", type=float, default=None)
    rmt.add_argument("--out", required=True)

    rhg = sub.add_parser("recommend-hysteresis")
    rhg.add_argument("--session", required=True)
    rhg.add_argument("--target-max-flip-rate", type=float, default=0.10)
    rhg.add_argument("--out", required=True)

    rrp = sub.add_parser("recommend-routing")
    rrp.add_argument("--sessions", nargs="+", required=True)
    rrp.add_argument("--max-offload-rate", type=float, default=0.30)
    rrp.add_argument("--out", required=True)

    padc = sub.add_parser("pad-compare")
    padc.add_argument("--sessions", nargs="+", required=True)
    padc.add_argument("--out", required=True)

    snap = sub.add_parser("operating-point")
    snap.add_argument("--session", required=True)
    snap.add_argument("--out", required=True)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if args.cmd == "compare-distributions":
        result = compare_confidence_distributions([Path(s) for s in args.sessions], key=args.key)
    elif args.cmd == "recommend-thresholds":
        result = recommend_match_thresholds(
            Path(args.session),
            target_matched_rate=args.target_matched_rate,
            target_offload_rate=args.target_offload_rate,
        )
    elif args.cmd == "recommend-hysteresis":
        result = recommend_hysteresis_gap(
            Path(args.session),
            target_max_flip_rate=args.target_max_flip_rate,
        )
    elif args.cmd == "recommend-routing":
        result = recommend_routing_policy(
            [Path(s) for s in args.sessions],
            max_offload_rate=args.max_offload_rate,
        )
    elif args.cmd == "pad-compare":
        result = pad_threshold_compare([Path(s) for s in args.sessions])
    elif args.cmd == "operating-point":
        result = operating_point_snapshot(Path(args.session))
    else:
        raise SystemExit(2)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
