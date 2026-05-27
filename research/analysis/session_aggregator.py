# research/analysis/session_aggregator.py
"""Multi-session aggregator — turn N session dirs into a comparison table.

Reads each session's ``summaries/stabilization_report.json`` (produced
by :mod:`research.analysis.stabilization_report`); if absent, falls back
to running :func:`research.analysis.stabilization_report.build_report`
on demand. Then projects a fixed set of comparison metrics into a
side-by-side table indexed by session.

CLI examples
------------
::

    python -m research.analysis.session_aggregator \\
        --sessions experiments/exp_a/ experiments/exp_b/ experiments/exp_c/ \\
        --out experiments/comparison.json

    # Group by an arbitrary dotted path into protocol/category
    python -m research.analysis.session_aggregator \\
        --sessions experiments/exp_*/ \\
        --group-by protocol.distance_m \\
        --out experiments/distance_grouped.json
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional


log = logging.getLogger("research.analysis.session_aggregator")


_COMPARISON_METRICS: List[Dict[str, Any]] = [
    {"key": "rows", "path": "rows", "type": "int"},
    {"key": "n_tracks", "path": "stabilization.orientation_stability.n_tracks", "type": "int"},
    {"key": "mode_flip_rate_mean", "path": "stabilization.orientation_stability.mode_flip_rate_mean", "type": "float"},
    {"key": "sim_std_mean", "path": "stabilization.confidence_stability.sim_std_mean", "type": "float"},
    {"key": "active_fraction_mean", "path": "stabilization.detection_persistence.mean_active_fraction", "type": "float"},
    {"key": "area_cv_mean", "path": "stabilization.bbox_stability.area_cv_mean", "type": "float"},
    {"key": "pad_real_fraction", "path": "stabilization.pad_temporal.overall.real_fraction", "type": "float"},
    {"key": "pad_spoof_fraction", "path": "stabilization.pad_temporal.overall.spoof_fraction", "type": "float"},
    {"key": "offload_trigger_rate", "path": "stabilization.offload_trigger.offload_trigger_rate", "type": "float"},
    {"key": "thermal_p95", "path": "stabilization.thermal.p95", "type": "float"},
    {"key": "blur_p50", "path": "stabilization.blur_geometry_quality.blur.p50", "type": "float"},
    {"key": "proximity_close_fraction", "path": "runtime_diagnostics.proximity.close_fraction", "type": "float"},
    {"key": "no_match_rate", "path": "runtime_diagnostics.missed_detection.no_match_rate", "type": "float"},
    {"key": "identity_max_distinct", "path": "runtime_diagnostics.identity_flicker.max_distinct", "type": "int"},
    {"key": "pad_hysteresis_flip_rate", "path": "runtime_diagnostics.pad_hysteresis.overall_flip_rate", "type": "float"},
    {"key": "tag_count", "path": "quality_tags.tag_count", "type": "int"},
    {"key": "tag_alert_count", "path": "quality_tags.by_severity.alert", "type": "int"},
    {"key": "tag_warn_count", "path": "quality_tags.by_severity.warn", "type": "int"},
]


# ── Path projection ──────────────────────────────────────────────────────────

def _get_path(payload: Dict[str, Any], path: str) -> Any:
    """Walk a dotted path. ``a.b.c`` -> payload['a']['b']['c']. Returns None on miss."""
    cur: Any = payload
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _load_report(session_dir: Path) -> Optional[Dict[str, Any]]:
    """Read summaries/stabilization_report.json or compute on demand."""
    candidate = session_dir / "summaries" / "stabilization_report.json"
    if candidate.exists():
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed to read %s: %s", candidate, exc)
    # Fall back to computing it.
    try:
        from research.analysis.stabilization_report import build_report
        return build_report(session_dir)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not build report for %s: %s", session_dir, exc)
        return None


# ── Aggregator ───────────────────────────────────────────────────────────────

def aggregate_sessions(
    session_dirs: List[Any],
    sweep_dimension: Optional[str] = None,
    comparison_metric_path: Optional[str] = None,
    group_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a side-by-side metric table across the given sessions.

    Args:
        session_dirs: list of paths to ``experiments/exp_<id>/``.
        sweep_dimension: optional, e.g. ``"distance_m"``; used purely
            for labelling output rows.
        comparison_metric_path: optional dotted path into the per-session
            report that the operator wants to highlight in the output.
            Indexing into list-of-dicts is supported via ``[]`` syntax
            (e.g. ``"runtime_diagnostics.orientation_vs_confidence.per_mode[].sim_mean"``).
        group_by: optional dotted path used to group sessions (e.g.
            ``"protocol.orientation"``).
    """
    rows: List[Dict[str, Any]] = []
    raw_reports: List[Dict[str, Any]] = []
    for sdir in session_dirs:
        sdir = Path(sdir)
        report = _load_report(sdir)
        if report is None:
            continue
        raw_reports.append({"session_dir": str(sdir), "report": report})
        row: Dict[str, Any] = {
            "session_id": report.get("session_id") or sdir.name,
            "session_dir": str(sdir),
        }
        # Group-by tag
        if group_by:
            row["group"] = _get_path(report, group_by)
        # Sweep label
        if sweep_dimension:
            row["sweep_dimension"] = sweep_dimension
            row["sweep_value"] = _get_path(report, f"protocol.{sweep_dimension}")
        # Comparison metric
        if comparison_metric_path:
            row["comparison_metric"] = comparison_metric_path
            row["comparison_value"] = _resolve_metric_path(report, comparison_metric_path)
        # Standard projection
        for spec in _COMPARISON_METRICS:
            row[spec["key"]] = _get_path(report, spec["path"])
        rows.append(row)

    grouping: Optional[Dict[str, List[str]]] = None
    if group_by:
        grouping = {}
        for r in rows:
            key = str(r.get("group"))
            grouping.setdefault(key, []).append(r["session_id"])

    return {
        "session_count": len(rows),
        "sweep_dimension": sweep_dimension,
        "comparison_metric_path": comparison_metric_path,
        "group_by": group_by,
        "rows": rows,
        "grouping": grouping,
    }


def _resolve_metric_path(report: Dict[str, Any], path: str) -> Any:
    """Walk ``path`` allowing ``[]`` to descend into list-of-dicts.

    e.g. ``"runtime_diagnostics.orientation_vs_confidence.per_mode[].sim_mean"``
    returns a dict ``{mode: sim_mean}`` so the comparison row can show one
    number per mode.
    """
    if "[]" not in path:
        return _get_path(report, path)
    head, _, tail = path.partition("[]")
    head = head.rstrip(".")
    list_node = _get_path(report, head)
    if not isinstance(list_node, list):
        return None
    tail = tail.lstrip(".")
    if not tail:
        return list_node
    # Use the first key in each list element as the "label" key.
    out: Dict[str, Any] = {}
    for item in list_node:
        if not isinstance(item, dict):
            continue
        label_keys = [k for k in item.keys() if k != tail]
        label = item.get(label_keys[0]) if label_keys else None
        out[str(label)] = _get_path(item, tail)
    return out


# ── Markdown rendering ───────────────────────────────────────────────────────

def render_markdown(payload: Dict[str, Any], max_metrics: int = 8) -> str:
    rows = payload.get("rows") or []
    if not rows:
        return "# Session aggregation\n\n_No sessions analysed._\n"
    metric_keys = [m["key"] for m in _COMPARISON_METRICS][:max_metrics]
    lines: List[str] = []
    lines.append("# Session aggregation")
    lines.append("")
    if payload.get("sweep_dimension"):
        lines.append(f"Sweep dimension: `{payload['sweep_dimension']}`.")
    if payload.get("comparison_metric_path"):
        lines.append(f"Comparison metric: `{payload['comparison_metric_path']}`.")
    lines.append(f"Sessions: **{payload['session_count']}**.")
    lines.append("")
    header = ["session_id"] + (["sweep_value"] if any("sweep_value" in r for r in rows) else []) + metric_keys
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for r in rows:
        cells = [str(r.get("session_id"))]
        if "sweep_value" in header:
            cells.append(str(r.get("sweep_value")))
        for k in metric_keys:
            v = r.get(k)
            if isinstance(v, float):
                cells.append(f"{v:.4g}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    if payload.get("grouping"):
        lines.append("")
        lines.append("## Grouping")
        lines.append("")
        for group, ids in payload["grouping"].items():
            lines.append(f"- `{group}`: {len(ids)} session(s) — {', '.join(ids)}")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--sessions", nargs="+", required=True,
                   help="paths to experiments/exp_<id>/ directories")
    p.add_argument("--sweep-dimension", default=None,
                   help="label the table by this protocol field (e.g. 'distance_m')")
    p.add_argument("--comparison-metric", default=None,
                   help="dotted path to the highlighted comparison metric")
    p.add_argument("--group-by", default=None,
                   help="dotted path to group sessions by (e.g. 'protocol.orientation')")
    p.add_argument("--out", default=None,
                   help="output JSON path (default: experiments/session_aggregation.json)")
    p.add_argument("--md", default=None,
                   help="output Markdown path")
    p.add_argument("--no-md", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    payload = aggregate_sessions(
        args.sessions,
        sweep_dimension=args.sweep_dimension,
        comparison_metric_path=args.comparison_metric,
        group_by=args.group_by,
    )

    out_path = Path(args.out) if args.out else Path("experiments/session_aggregation.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s (sessions=%d)", out_path, payload["session_count"])

    if not args.no_md:
        md_path = Path(args.md) if args.md else out_path.with_suffix(".md")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
        log.info("wrote %s", md_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
