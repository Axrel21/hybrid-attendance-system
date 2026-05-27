# research/analysis/stabilization_report.py
"""Combined stabilization report bundler.

Single CLI entry that runs every offline analyzer against a session
directory and produces:

* ``experiments/exp_<id>/summaries/stabilization_report.json`` — bundled
  JSON containing the protocol sidecar (if present), the eight-dimension
  stabilization summary, runtime diagnostics, threshold sweep, and the
  quality-tag list.
* Optional ``stabilization_report.md`` — short human-readable summary
  (alert/warn tag list + headline metrics).

Convenience wrapper around:

* :mod:`research.analysis.stabilization`
* :mod:`research.analysis.runtime_diagnostics`
* :mod:`research.analysis.threshold_sweep`
* :mod:`research.analysis.quality_gates`
* :mod:`research.experiment_protocol`

Nothing here is novel logic; each underlying module can still be run
standalone. This module exists so a single command produces "everything"
for a session.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.analysis.quality_gates import GateThresholds, evaluate_metrics
from research.analysis.runtime_diagnostics import diagnose_session
from research.analysis.stabilization import summarize_session
from research.analysis.threshold_sweep import sweep_session
from research.experiment_protocol import load_protocol


log = logging.getLogger("research.analysis.stabilization_report")


# ── Markdown rendering ───────────────────────────────────────────────────────

def _md_section(title: str, body: str = "") -> str:
    return f"## {title}\n\n{body}\n" if body else f"## {title}\n\n_no data_\n"


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Stabilization report — `{report['session_id']}`")
    lines.append("")
    lines.append(f"Rows analysed: **{report['rows']}**.")
    proto = report.get("protocol")
    if proto:
        bits = []
        for key in ("experiment_label", "attack_type", "distance_m", "lighting",
                    "orientation", "mounting", "movement"):
            v = proto.get(key)
            if v not in (None, ""):
                bits.append(f"`{key}`={v}")
        if bits:
            lines.append(f"Protocol: {', '.join(bits)}.")
    lines.append("")

    tags = report.get("quality_tags", {}).get("tags") or []
    if tags:
        lines.append("## Quality tags")
        lines.append("")
        lines.append("| severity | tag | value | threshold | detail |")
        lines.append("|---|---|---|---|---|")
        for t in tags:
            v = t.get("value")
            th = t.get("threshold")
            v_str = f"{v:.4g}" if isinstance(v, (int, float)) else str(v)
            th_str = f"{th:.4g}" if isinstance(th, (int, float)) else str(th)
            lines.append(f"| {t['severity']} | `{t['tag']}` | {v_str} | {th_str} | {t.get('detail','')} |")
    else:
        lines.append("## Quality tags")
        lines.append("")
        lines.append("_No tags raised. Session is within all default thresholds._")
    lines.append("")

    stab = report.get("stabilization", {})
    persist = stab.get("detection_persistence", {})
    pad = stab.get("pad_temporal", {}).get("overall", {})
    off = stab.get("offload_trigger", {})
    therm = stab.get("thermal", {})
    headline = [
        f"- tracks: **{stab.get('orientation_stability',{}).get('n_tracks', 0)}**",
        f"- mean active-frame fraction: **{persist.get('mean_active_fraction', 0):.3f}**",
        f"- PAD real / spoof / uncertain: "
        f"**{pad.get('real_fraction',0):.2f}** / **{pad.get('spoof_fraction',0):.2f}** / "
        f"**{pad.get('uncertain_fraction',0):.2f}**",
        f"- offload trigger rate: **{off.get('offload_trigger_rate', 0):.3f}**",
        f"- thermal p95: **{therm.get('p95', 0):.1f} °C**",
        f"- mean orientation mode-flip rate: "
        f"**{stab.get('orientation_stability',{}).get('mode_flip_rate_mean', 0):.3f}**",
    ]
    lines.append("## Headline metrics")
    lines.append("")
    lines.extend(headline)
    lines.append("")

    runtime = report.get("runtime_diagnostics", {})
    if runtime:
        rid_lines = []
        prox = runtime.get("proximity", {})
        if prox.get("n"):
            rid_lines.append(f"- proximity close-fraction: **{prox.get('close_fraction', 0):.3f}**, "
                             f"out-of-range fraction: **{prox.get('out_of_range_fraction', 0):.3f}**")
        miss = runtime.get("missed_detection", {})
        if miss.get("n"):
            rid_lines.append(f"- NO_MATCH rate: **{miss.get('no_match_rate', 0):.3f}**, "
                             f"OUT_OF_RANGE rate: **{miss.get('out_of_range_rate', 0):.3f}**")
        flick = runtime.get("identity_flicker", {})
        if flick.get("n_tracks"):
            rid_lines.append(f"- max distinct identities per track: "
                             f"**{flick.get('max_distinct', 0)}**")
        if rid_lines:
            lines.append("## Runtime diagnostics")
            lines.append("")
            lines.extend(rid_lines)
            lines.append("")

    sweep = report.get("threshold_sweep", {}).get("match_threshold_sweep") or []
    if sweep:
        lines.append("## Match-threshold sweep (snapshot)")
        lines.append("")
        lines.append("| th_high | matched | offload | below |")
        lines.append("|---|---|---|---|")
        for p in sweep[:: max(1, len(sweep) // 6)]:  # show ~6 points
            lines.append(
                f"| {p['th_high']:.2f} | {p['matched_rate']:.3f} "
                f"| {p['offload_rate']:.3f} | {p['below_threshold_rate']:.3f} |"
            )
        lines.append("")

    return "\n".join(lines)


# ── Top-level driver ─────────────────────────────────────────────────────────

def build_report(
    session_dir: Path,
    thresholds: Optional[GateThresholds] = None,
    th_high_min: float = 0.50,
    th_high_max: float = 0.95,
    sweep_steps: int = 19,
) -> Dict[str, Any]:
    session_dir = Path(session_dir).resolve()
    diag = session_dir / "diagnostics" / "diagnostic_log.csv"
    if not diag.exists():
        raise FileNotFoundError(f"diagnostic CSV not found: {diag}")

    stabilization = summarize_session(diag)
    runtime = diagnose_session(diag)
    sweep = sweep_session(
        diag,
        th_high_range=(th_high_min, th_high_max),
        steps=sweep_steps,
    )

    # Re-use quality_gates.evaluate_metrics — operates on the dicts.
    # ``low_light`` requires raw brightness; pull from already-loaded df.
    import pandas as pd
    df = pd.read_csv(diag)
    tags = evaluate_metrics(stabilization, runtime, thresholds=thresholds)
    if "brightness" in df.columns:
        b = pd.to_numeric(df["brightness"], errors="coerce").dropna()
        if len(b):
            from research.analysis.quality_gates import _eval_pair
            th = thresholds or GateThresholds()
            t = _eval_pair(
                "low_light", float(b.median()),
                th.get("brightness_p50_warn"), th.get("brightness_p50_alert"),
                "lt", "median brightness across all frames",
            )
            if t:
                tags.append(t)

    quality_payload = {
        "session_id": session_dir.name,
        "rows": int(len(df)),
        "tags": tags,
        "tag_count": len(tags),
        "by_severity": {
            sev: sum(1 for t in tags if t["severity"] == sev)
            for sev in ("info", "warn", "alert")
        },
    }

    proto = load_protocol(session_dir)
    return {
        "session_id": session_dir.name,
        "session_dir": str(session_dir),
        "rows": int(len(df)),
        "protocol": proto.to_dict() if proto is not None else None,
        "stabilization": stabilization,
        "runtime_diagnostics": runtime,
        "threshold_sweep": sweep,
        "quality_tags": quality_payload,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_overrides(raw: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for item in raw or []:
        if "=" not in item:
            raise ValueError(f"--threshold expects KEY=VALUE; got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--session", required=True, help="path to experiments/exp_<id>/")
    p.add_argument("--out", default=None,
                   help="output JSON path (default: <session>/summaries/stabilization_report.json)")
    p.add_argument("--md", default=None,
                   help="output Markdown path (default: <session>/summaries/stabilization_report.md)")
    p.add_argument("--no-md", action="store_true",
                   help="skip Markdown rendering")
    p.add_argument("--threshold", action="append", default=[], metavar="KEY=VALUE",
                   help="override a gate threshold")
    p.add_argument("--th-high-min", type=float, default=0.50)
    p.add_argument("--th-high-max", type=float, default=0.95)
    p.add_argument("--sweep-steps", type=int, default=19)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    session_dir = Path(args.session)
    thresholds = GateThresholds(overrides=_parse_overrides(args.threshold))
    report = build_report(
        session_dir,
        thresholds=thresholds,
        th_high_min=args.th_high_min,
        th_high_max=args.th_high_max,
        sweep_steps=args.sweep_steps,
    )

    out_path = Path(args.out) if args.out else session_dir / "summaries" / "stabilization_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s (rows=%d, tags=%d)", out_path,
             report["rows"], report["quality_tags"]["tag_count"])

    if not args.no_md:
        md_path = Path(args.md) if args.md else session_dir / "summaries" / "stabilization_report.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report), encoding="utf-8")
        log.info("wrote %s", md_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
