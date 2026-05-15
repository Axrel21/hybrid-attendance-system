# research/experiments/sweep_orchestrator.py
"""Sweep orchestrator — load a preset, plan operator runs, drive analysis.

This module **does not** start the camera or capture frames. It exists
to turn a named sweep preset into:

1. A printable plan: one row per intended run, with the protocol args
   and env vars the operator should use.
2. After captures complete: per-session analysis + cross-session
   aggregation, all bundled into one JSON + Markdown output.

CLI examples
------------
::

    # 1. Print the plan for a distance sweep
    python -m research.experiments.sweep_orchestrator \\
        --preset distance_sweep --plan

    # 2. After running each capture and tagging it with the protocol
    #    CLI, aggregate the resulting sessions
    python -m research.experiments.sweep_orchestrator \\
        --preset distance_sweep \\
        --sessions experiments/exp_a/ experiments/exp_b/ ... \\
        --out experiments/sweep_distance_2026-05-16/

Presets live under ``research/experiments/presets/<name>.json``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from shared.contracts import PRESET_NAMES, PRESET_VERSION
except Exception:  # noqa: BLE001
    PRESET_NAMES = ()
    PRESET_VERSION = "1.0"


log = logging.getLogger("research.experiments.sweep_orchestrator")


_PRESETS_DIR = Path(__file__).resolve().parent / "presets"


# ── Preset I/O ────────────────────────────────────────────────────────────────

def list_presets() -> List[str]:
    return sorted(p.stem for p in _PRESETS_DIR.glob("*.json"))


def load_preset(name: str) -> Dict[str, Any]:
    path = _PRESETS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"unknown preset {name!r}; available: {list_presets()}"
        )
    with open(path, "r", encoding="utf-8") as fh:
        preset = json.load(fh)
    if preset.get("preset") != name:
        raise ValueError(f"preset file {path} has mismatched preset name")
    return preset


# ── Plan generation ──────────────────────────────────────────────────────────

@dataclass
class PlannedRun:
    index: int
    sweep_value: Any
    protocol_overrides: Dict[str, Any]
    env_overrides: Dict[str, str]
    duration_s: int
    operator_cmd: str       # suggested shell snippet
    protocol_cmd: str       # python -m research.experiment_protocol invocation


def plan_runs(preset: Dict[str, Any]) -> List[PlannedRun]:
    dim = preset["sweep_dimension"]
    fixed = dict(preset.get("fixed_protocol", {}))
    fixed_env = dict(preset.get("fixed_env", {}))
    duration = int(preset.get("recommended_duration_s", 120))

    plan: List[PlannedRun] = []
    for i, value in enumerate(preset.get("sweep_values", []), start=1):
        proto = dict(fixed)
        env = dict(fixed_env)

        # Slot the sweep value into the right field. ``th_high`` is special
        # because it's a post-hoc analysis knob, not a capture-time setting.
        if dim == "th_high":
            pass  # one capture only; sweep happens offline
        elif dim == "CLOUD_ROUTING":
            env["CLOUD_ROUTING"] = str(value)
        elif dim in (
            "attack_type", "lighting", "orientation",
            "mounting", "movement", "dataset_label",
        ):
            proto[dim] = str(value)
        elif dim == "distance_m":
            proto["distance_m"] = float(value)
        else:
            proto[dim] = value

        env_str = " ".join(f"{k}={v}" for k, v in env.items())
        proto_args = []
        for k, v in proto.items():
            cli_flag = "--" + k.replace("_", "-").replace("-m", "") if k == "distance_m" else "--" + k.replace("_", "-")
            if k == "distance_m":
                cli_flag = "--distance"
            proto_args.append(f"{cli_flag} {v}")
        proto_cmd = (
            "python -m research.experiment_protocol --session experiments/<exp_id>/ "
            + " ".join(proto_args)
        )
        run_cmd = (
            f"{env_str + ' ' if env_str else ''}"
            f"timeout {duration} python run.py   # capture # {i} ({dim}={value})"
        )
        plan.append(PlannedRun(
            index=i, sweep_value=value,
            protocol_overrides=proto, env_overrides=env,
            duration_s=duration, operator_cmd=run_cmd, protocol_cmd=proto_cmd,
        ))
    return plan


def render_plan(preset: Dict[str, Any], plan: List[PlannedRun]) -> str:
    lines: List[str] = []
    lines.append(f"# Sweep plan — preset `{preset['preset']}` v{preset.get('preset_version','?')}")
    lines.append("")
    lines.append(preset.get("description", ""))
    lines.append("")
    lines.append(f"**Sweep dimension:** `{preset['sweep_dimension']}`")
    lines.append(f"**Recommended duration per run:** {preset.get('recommended_duration_s', '?')} s")
    lines.append("")
    if preset.get("notes"):
        lines.append("> " + preset["notes"].replace("\n", "\n> "))
        lines.append("")
    for run in plan:
        lines.append(f"## Run {run.index} — {preset['sweep_dimension']} = `{run.sweep_value}`")
        lines.append("")
        lines.append("```bash")
        lines.append(f"# 1. Start the capture")
        lines.append(run.operator_cmd)
        lines.append("")
        lines.append(f"# 2. After the run, find the new experiments/exp_<id>/ and tag it")
        lines.append(run.protocol_cmd)
        lines.append("```")
        lines.append("")
    lines.append("## After all runs complete")
    lines.append("")
    lines.append("```bash")
    lines.append(
        "python -m research.experiments.sweep_orchestrator \\"
    )
    lines.append(
        f"    --preset {preset['preset']} \\"
    )
    lines.append(
        "    --sessions experiments/exp_<id_1>/ experiments/exp_<id_2>/ ..."
    )
    lines.append("```")
    return "\n".join(lines)


# ── Analysis driver ──────────────────────────────────────────────────────────

def aggregate_after_capture(
    preset: Dict[str, Any],
    session_dirs: List[Path],
    out_dir: Path,
) -> Dict[str, Any]:
    """Run per-session analyzers + cross-session aggregation.

    Delegates to research.analysis.session_aggregator for the actual
    table-building. Returns a dict containing the preset, the per-session
    summaries, and the aggregated comparison.
    """
    from research.analysis.session_aggregator import aggregate_sessions
    from research.analysis.stabilization_report import build_report

    out_dir.mkdir(parents=True, exist_ok=True)
    per_session: List[Dict[str, Any]] = []
    for sdir in session_dirs:
        sdir = Path(sdir)
        log.info("analysing %s", sdir)
        try:
            report = build_report(sdir)
        except FileNotFoundError as exc:
            log.warning("skipping %s: %s", sdir, exc)
            continue
        per_session.append({
            "session_dir": str(sdir),
            "session_id": report["session_id"],
            "report": report,
        })

    aggregation = aggregate_sessions(
        [s["session_dir"] for s in per_session],
        sweep_dimension=preset.get("sweep_dimension"),
        comparison_metric_path=preset.get("comparison_metric"),
    )

    payload = {
        "preset": preset["preset"],
        "preset_version": preset.get("preset_version", PRESET_VERSION),
        "sweep_dimension": preset.get("sweep_dimension"),
        "sessions_total": len(per_session),
        "per_session": [
            {
                "session_id": s["session_id"],
                "session_dir": s["session_dir"],
                "rows": s["report"].get("rows"),
                "quality_tags": s["report"].get("quality_tags", {}).get("by_severity", {}),
                "protocol": s["report"].get("protocol"),
            }
            for s in per_session
        ],
        "aggregation": aggregation,
    }

    out_path = out_dir / f"sweep_{preset['preset']}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    log.info("wrote %s (sessions=%d)", out_path, len(per_session))
    return payload


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--preset", required=True, help=f"preset name; choose from {PRESET_NAMES}")
    p.add_argument("--plan", action="store_true",
                   help="print the operator plan (no analysis)")
    p.add_argument("--list", action="store_true",
                   help="list available presets and exit")
    p.add_argument("--sessions", nargs="*", default=None,
                   help="session directories to aggregate after captures complete")
    p.add_argument("--out", default=None,
                   help="output dir for aggregated payload (default: experiments/sweep_<preset>/)")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if args.list:
        for name in list_presets():
            print(name)
        return 0

    preset = load_preset(args.preset)

    if args.plan or not args.sessions:
        plan = plan_runs(preset)
        print(render_plan(preset, plan))
        if not args.sessions:
            return 0

    session_dirs = [Path(s) for s in args.sessions]
    out_dir = Path(args.out) if args.out else Path(f"experiments/sweep_{preset['preset']}")
    aggregate_after_capture(preset, session_dirs, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
