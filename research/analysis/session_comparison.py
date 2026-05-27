# research/analysis/session_comparison.py
"""Pairwise (baseline vs modified) session comparison.

Produces a side-by-side diff of metrics between two sessions, plus a
delta of triggered quality tags. The output is a small JSON + Markdown
pair suitable for inclusion in a calibration write-up.

CLI examples
------------
::

    python -m research.analysis.session_comparison \\
        --baseline experiments/exp_baseline/ \\
        --modified experiments/exp_tuned/ \\
        --out experiments/comparison_baseline_vs_tuned.json
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional


log = logging.getLogger("research.analysis.session_comparison")


_METRIC_KEYS: List[Dict[str, Any]] = [
    {"key": "rows", "path": "rows"},
    {"key": "n_tracks", "path": "stabilization.orientation_stability.n_tracks"},
    {"key": "mode_flip_rate_mean", "path": "stabilization.orientation_stability.mode_flip_rate_mean"},
    {"key": "sim_std_mean", "path": "stabilization.confidence_stability.sim_std_mean"},
    {"key": "active_fraction_mean", "path": "stabilization.detection_persistence.mean_active_fraction"},
    {"key": "area_cv_mean", "path": "stabilization.bbox_stability.area_cv_mean"},
    {"key": "pad_real_fraction", "path": "stabilization.pad_temporal.overall.real_fraction"},
    {"key": "pad_spoof_fraction", "path": "stabilization.pad_temporal.overall.spoof_fraction"},
    {"key": "offload_trigger_rate", "path": "stabilization.offload_trigger.offload_trigger_rate"},
    {"key": "thermal_p95", "path": "stabilization.thermal.p95"},
    {"key": "blur_p50", "path": "stabilization.blur_geometry_quality.blur.p50"},
    {"key": "proximity_close_fraction", "path": "runtime_diagnostics.proximity.close_fraction"},
    {"key": "no_match_rate", "path": "runtime_diagnostics.missed_detection.no_match_rate"},
    {"key": "identity_max_distinct", "path": "runtime_diagnostics.identity_flicker.max_distinct"},
    {"key": "pad_hysteresis_flip_rate", "path": "runtime_diagnostics.pad_hysteresis.overall_flip_rate"},
    {"key": "tag_count", "path": "quality_tags.tag_count"},
]


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


def _load_report(session_dir: Path) -> Optional[Dict[str, Any]]:
    rp = session_dir / "summaries" / "stabilization_report.json"
    if rp.exists():
        try:
            with open(rp, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed to read %s: %s", rp, exc)
    try:
        from research.analysis.stabilization_report import build_report
        return build_report(session_dir)
    except Exception as exc:  # noqa: BLE001
        log.error("Could not build report for %s: %s", session_dir, exc)
        return None


def compare(baseline_dir: Path, modified_dir: Path) -> Dict[str, Any]:
    baseline = _load_report(Path(baseline_dir))
    modified = _load_report(Path(modified_dir))
    if baseline is None or modified is None:
        raise RuntimeError("could not load both reports")

    rows: List[Dict[str, Any]] = []
    for spec in _METRIC_KEYS:
        va = _g(baseline, spec["path"])
        vb = _g(modified, spec["path"])
        delta = None
        rel = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = float(vb) - float(va)
            anchor = max(abs(float(va)), 1e-9)
            rel = delta / anchor
        rows.append({
            "metric": spec["key"],
            "session_a": baseline.get("session_id"),
            "session_b": modified.get("session_id"),
            "value_a": va,
            "value_b": vb,
            "delta": delta,
            "rel_change": rel,
            "detail": spec["path"],
        })

    # Tag delta
    a_tags = {t["tag"] for t in (baseline.get("quality_tags") or {}).get("tags", [])}
    b_tags = {t["tag"] for t in (modified.get("quality_tags") or {}).get("tags", [])}
    return {
        "session_a": baseline.get("session_id"),
        "session_b": modified.get("session_id"),
        "metrics": rows,
        "tags_only_in_a": sorted(a_tags - b_tags),
        "tags_only_in_b": sorted(b_tags - a_tags),
        "tags_in_both": sorted(a_tags & b_tags),
        "protocol_a": baseline.get("protocol"),
        "protocol_b": modified.get("protocol"),
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    a = payload["session_a"]
    b = payload["session_b"]
    lines: List[str] = []
    lines.append(f"# Session comparison — `{a}` (baseline) vs `{b}` (modified)")
    lines.append("")
    lines.append("| metric | baseline | modified | delta | rel change |")
    lines.append("|---|---|---|---|---|")
    for r in payload["metrics"]:
        va = r["value_a"]
        vb = r["value_b"]
        d = r["delta"]
        rel = r["rel_change"]
        va_s = f"{va:.4g}" if isinstance(va, float) else str(va)
        vb_s = f"{vb:.4g}" if isinstance(vb, float) else str(vb)
        d_s = f"{d:+.4g}" if isinstance(d, float) else str(d)
        rel_s = f"{rel:+.2%}" if isinstance(rel, float) else str(rel)
        lines.append(f"| `{r['metric']}` | {va_s} | {vb_s} | {d_s} | {rel_s} |")
    lines.append("")
    if payload["tags_only_in_a"]:
        lines.append(f"### Tags resolved (present in baseline, absent in modified)")
        lines.append("")
        lines.append(", ".join(f"`{t}`" for t in payload["tags_only_in_a"]))
        lines.append("")
    if payload["tags_only_in_b"]:
        lines.append(f"### Tags introduced (absent in baseline, present in modified)")
        lines.append("")
        lines.append(", ".join(f"`{t}`" for t in payload["tags_only_in_b"]))
        lines.append("")
    if payload["tags_in_both"]:
        lines.append(f"### Tags present in both")
        lines.append("")
        lines.append(", ".join(f"`{t}`" for t in payload["tags_in_both"]))
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--baseline", required=True, help="baseline session dir")
    p.add_argument("--modified", required=True, help="modified session dir")
    p.add_argument("--out", default=None,
                   help="output JSON path (default: experiments/comparison_<a>_vs_<b>.json)")
    p.add_argument("--no-md", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    payload = compare(Path(args.baseline), Path(args.modified))
    out_path = Path(args.out) if args.out else Path(
        f"experiments/comparison_{payload['session_a']}_vs_{payload['session_b']}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s", out_path)
    if not args.no_md:
        md_path = out_path.with_suffix(".md")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
        log.info("wrote %s", md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
