# edge/experiment_report.py
"""
Post-run experiment report: load session CSVs, metadata, and logs;
write PNG plots to experiments/<id>/plots/ and JSON+MD summaries to summaries/.

Uses matplotlib Agg backend (headless-safe). Optional imports: if pandas/matplotlib
are missing, generation is skipped with a log line (pipeline stays healthy).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

_LOG = logging.getLogger("attendance.runtime")


def _try_import_reporting():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: WPS433
    import pandas as pd  # noqa: WPS433

    return plt, pd


def _style_axes(ax, xlabel: str, ylabel: str, title: str) -> None:
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)


def _savefig(path: Path, plt_module) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt_module.tight_layout()
    plt_module.savefig(path, dpi=150, bbox_inches="tight")
    plt_module.close()


def generate_experiment_report(
    experiment_root: Optional[str] = None,
    *,
    paths: Any = None,
) -> Optional[dict[str, Any]]:
    """
    Build plots and summary artifacts for the experiment at EXPERIMENT_ROOT or
    ``paths.root`` from config.experiment_session.ExperimentPaths.

    Returns a stats dict on success, None if skipped or failed softly.
    """
    try:
        plt, pd = _try_import_reporting()
    except ImportError as exc:
        _LOG.warning(
            "Experiment report skipped (install pandas matplotlib): %s",
            exc,
        )
        return None

    if paths is not None:
        root = Path(paths.root)
        tel_csv = Path(paths.telemetry_csv)
        diag_csv = Path(paths.diagnostic_csv)
        meta_json = Path(paths.config_dir) / "settings_snapshot.json"
        plots_dir = Path(paths.plots_dir)
        summ_dir = Path(paths.summaries_dir)
        runtime_log = Path(paths.runtime_log_path)
    elif experiment_root:
        root = Path(experiment_root)
        tel_csv = root / "telemetry" / "telemetry_log.csv"
        diag_csv = root / "diagnostics" / "diagnostic_log.csv"
        meta_json = root / "config" / "settings_snapshot.json"
        plots_dir = root / "plots"
        summ_dir = root / "summaries"
        runtime_log = root / "logs" / "runtime.log"
    else:
        from config.experiment_session import get_current_paths

        p = get_current_paths()
        if p is None:
            _LOG.warning("Experiment report skipped (no active experiment session).")
            return None
        return generate_experiment_report(paths=p)

    batch_ts = time.strftime("%Y%m%d_%H%M%S")
    plots_dir.mkdir(parents=True, exist_ok=True)
    summ_dir.mkdir(parents=True, exist_ok=True)

    stats: dict[str, Any] = {
        "batch_timestamp": batch_ts,
        "experiment_root": str(root),
        "telemetry_csv": str(tel_csv),
        "diagnostic_csv": str(diag_csv),
    }

    tel = None
    diag = None
    meta: dict = {}

    if tel_csv.is_file() and tel_csv.stat().st_size > 0:
        try:
            tel = pd.read_csv(tel_csv)
            stats["telemetry_rows"] = int(len(tel))
        except Exception as exc:
            _LOG.warning("Could not read telemetry CSV: %s", exc)
    else:
        stats["telemetry_rows"] = 0

    if diag_csv.is_file() and diag_csv.stat().st_size > 0:
        try:
            diag = pd.read_csv(diag_csv)
            stats["diagnostic_rows"] = int(len(diag))
        except Exception as exc:
            _LOG.warning("Could not read diagnostic CSV: %s", exc)
    else:
        stats["diagnostic_rows"] = 0

    if meta_json.is_file():
        try:
            with open(meta_json, encoding="utf-8") as f:
                meta = json.load(f)
            stats["metadata"] = {"experiment_id": meta.get("experiment_id")}
        except Exception:
            stats["metadata"] = {}

    # --- Derived time bases ---
    if tel is not None and len(tel) > 0:
        t0 = float(tel["timestamp"].iloc[0])
        tel["_t_rel_s"] = tel["timestamp"].astype(float) - t0

        # Performance plots
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["fps_rolling"], lw=0.8, color="#1f77b4")
        _style_axes(ax, "Time (s from start)", "Rolling FPS", "FPS vs time")
        _savefig(plots_dir / f"report_{batch_ts}_perf_fps.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["t_total_ms"], lw=0.7, color="#d62728", alpha=0.85)
        _style_axes(ax, "Time (s)", "Total frame latency (ms)", "Pipeline latency vs time")
        _savefig(plots_dir / f"report_{batch_ts}_perf_total_latency.png", plt)

        for col, slug, title, ylabel in (
            ("t_detect_ms", "detect", "YuNet / detection latency", "ms"),
            ("t_embed_max_ms", "embed", "Embedding latency (max/track, frame)", "ms"),
            ("t_liveness_max_ms", "liveness", "Liveness latency (max/track, frame)", "ms"),
        ):
            if col in tel.columns:
                fig, ax = plt.subplots(figsize=(9, 3.5))
                ax.plot(tel["_t_rel_s"], tel[col], lw=0.7, alpha=0.85)
                _style_axes(ax, "Time (s)", ylabel, title)
                _savefig(plots_dir / f"report_{batch_ts}_perf_{slug}.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["cpu_pct"], lw=0.7, label="CPU %", color="#2ca02c")
        _style_axes(ax, "Time (s)", "CPU %", "CPU usage vs time")
        _savefig(plots_dir / f"report_{batch_ts}_perf_cpu.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["mem_mb"], lw=0.7, color="#9467bd")
        _style_axes(ax, "Time (s)", "RSS (MB)", "Memory usage vs time")
        _savefig(plots_dir / f"report_{batch_ts}_perf_mem.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["cpu_temp_c"], lw=0.7, color="#ff7f0e")
        _style_axes(ax, "Time (s)", "Temperature (°C)", "SoC temperature vs time")
        _savefig(plots_dir / f"report_{batch_ts}_perf_temp.png", plt)

        # Summary stats from telemetry
        stats["fps_mean"] = float(tel["fps_rolling"].mean())
        stats["fps_median"] = float(tel["fps_rolling"].median())
        stats["latency_total_ms_mean"] = float(tel["t_total_ms"].mean())
        stats["latency_total_ms_peak"] = float(tel["t_total_ms"].max())
        stats["latency_total_ms_p99"] = float(tel["t_total_ms"].quantile(0.99))
        ct_mean = float(
            np.nanmean(
                np.where(
                    tel["cpu_temp_c"].to_numpy(dtype=float) > 0,
                    tel["cpu_temp_c"].to_numpy(dtype=float),
                    np.nan,
                )
            )
        )
        stats["cpu_temp_mean"] = None if np.isnan(ct_mean) else ct_mean
        stats["cpu_temp_max"] = float(tel["cpu_temp_c"].max())

        if "dt_ms" in tel.columns:
            dt = tel["dt_ms"].astype(float)
            dt_pos = dt[dt > 0]
            med = float(dt_pos.median()) if len(dt_pos) else 1.0
            thr = max(med * 2.0, med + 50.0)
            dropped = int((dt_pos > thr).sum())
            stats["dropped_frames_heuristic"] = dropped
            stats["dropped_frame_threshold_ms"] = thr
            stats["median_frame_interval_ms"] = med

    if diag is not None and len(diag) > 0:
        diag = diag.sort_values("timestamp")
        diag["_t_rel_s"] = diag["timestamp"].astype(float) - float(diag["timestamp"].iloc[0])

        if "live_conf" in diag.columns:
            g = diag.groupby("timestamp", as_index=False)["live_conf"].mean()
            g["_t_rel_s"] = g["timestamp"].astype(float) - float(diag["timestamp"].iloc[0])
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(g["_t_rel_s"], g["live_conf"], drawstyle="steps-post", lw=0.9, color="#17becf")
            _style_axes(
                ax,
                "Time (s)",
                "Mean live_conf (per timestamp)",
                "Liveness confidence over time",
            )
            _savefig(plots_dir / f"report_{batch_ts}_pad_live_conf.png", plt)

        if "rigid_ratio" in diag.columns:
            g2 = diag.groupby("timestamp", as_index=False)["rigid_ratio"].mean()
            g2["_t_rel_s"] = g2["timestamp"].astype(float) - float(diag["timestamp"].iloc[0])
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(g2["_t_rel_s"], g2["rigid_ratio"], lw=0.8, color="#8c564b")
            _style_axes(ax, "Time (s)", "Mean rigid_ratio", "Rigid ratio over time")
            _savefig(plots_dir / f"report_{batch_ts}_pad_rigid_ratio.png", plt)

        if "lbl" in diag.columns:
            vc = diag["lbl"].value_counts()
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.bar(vc.index.astype(str), vc.values, color="#bcbd22", edgecolor="black")
            _style_axes(ax, "Label", "Count", "Liveness label distribution")
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
            _savefig(plots_dir / f"report_{batch_ts}_pad_label_dist.png", plt)
            stats["spoof_rows"] = int((diag["lbl"] == "SPOOF").sum())
            stats["real_rows"] = int((diag["lbl"] == "REAL").sum())

        if "sim" in diag.columns:
            sreal = diag.loc[diag["lbl"] == "REAL", "sim"].dropna()
            sspoof = diag.loc[diag["lbl"] == "SPOOF", "sim"].dropna()
            fig, ax = plt.subplots(figsize=(8, 4))
            if len(sreal):
                ax.hist(sreal, bins=40, alpha=0.6, label="REAL", color="green", density=True)
            if len(sspoof):
                ax.hist(sspoof, bins=40, alpha=0.6, label="SPOOF", color="red", density=True)
            ax.legend()
            _style_axes(ax, "Similarity", "Density", "Similarity distribution (REAL vs SPOOF)")
            _savefig(plots_dir / f"report_{batch_ts}_rec_sim_by_label.png", plt)

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.hist(diag["sim"].dropna(), bins=50, color="#7f7f7f", edgecolor="white")
            _style_axes(ax, "Similarity", "Count", "Similarity histogram (all rows)")
            _savefig(plots_dir / f"report_{batch_ts}_rec_sim_hist.png", plt)

        if "sim" in diag.columns and "th_high" in diag.columns:
            sample = diag[["sim", "th_high"]].dropna().sample(
                min(5000, len(diag)),
                random_state=0,
            )
            fig, ax = plt.subplots(figsize=(7, 7))
            ax.scatter(sample["th_high"], sample["sim"], s=4, alpha=0.35, c="#1f77b4")
            lim = [0, 1.05]
            ax.plot(lim, lim, "k--", lw=0.8, alpha=0.5, label="sim = th_high")
            ax.set_xlim(lim)
            ax.set_ylim(lim)
            ax.legend()
            _style_axes(
                ax,
                "Adaptive th_high",
                "Similarity",
                "Similarity vs match threshold",
            )
            _savefig(plots_dir / f"report_{batch_ts}_rec_threshold_scatter.png", plt)

        if "sim" in diag.columns and "track_id" in diag.columns:
            real = diag[diag["lbl"] == "REAL"].copy()
            if len(real) > 5:
                std_by = real.groupby("track_id")["sim"].std().dropna()
                fig, ax = plt.subplots(figsize=(8, 4))
                ax.hist(std_by, bins=min(30, max(5, len(std_by) // 2)), color="#e377c2")
                _style_axes(
                    ax,
                    "Std(sim) per track",
                    "Number of tracks",
                    "Recognition confidence stability (REAL)",
                )
                _savefig(plots_dir / f"report_{batch_ts}_rec_conf_stability.png", plt)
                stats["sim_std_mean_over_tracks"] = float(std_by.mean()) if len(std_by) else None
            else:
                stats["sim_std_mean_over_tracks"] = None

        if "g_score" in diag.columns and "lbl" in diag.columns:
            fig, ax = plt.subplots(figsize=(8, 4))
            for lbl, color in (("REAL", "green"), ("SPOOF", "red")):
                sub = diag.loc[diag["lbl"] == lbl, "g_score"].dropna()
                if len(sub):
                    ax.hist(sub, bins=30, alpha=0.5, label=lbl, density=True, color=color)
            ax.legend()
            _style_axes(
                ax,
                "Geometry score (g_score)",
                "Density",
                "Geometry / PAD-related score by label",
            )
            _savefig(plots_dir / f"report_{batch_ts}_pad_g_score_dist.png", plt)

    # Log preview (tail)
    log_lines = 0
    if runtime_log.is_file():
        try:
            text = runtime_log.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            log_lines = len(lines)
            stats["runtime_log_lines"] = log_lines
            stats["runtime_log_tail"] = "\n".join(lines[-40:]) if lines else ""
        except Exception:
            stats["runtime_log_tail"] = ""

    json_path = summ_dir / f"report_{batch_ts}.json"
    with open(json_path, "w", encoding="utf-8") as jf:
        # Drop huge tail from JSON copy
        outj = {k: v for k, v in stats.items() if k != "runtime_log_tail"}
        outj["runtime_log_lines"] = stats.get("runtime_log_lines", 0)
        json.dump(outj, jf, indent=2, default=str)

    md_path = summ_dir / f"report_{batch_ts}.md"
    lines_md = [
        f"# Experiment report `{batch_ts}`",
        "",
        f"- Root: `{root}`",
        f"- Telemetry rows: **{stats.get('telemetry_rows', 0)}**",
        f"- Diagnostic rows: **{stats.get('diagnostic_rows', 0)}**",
        "",
        "## Summary",
        "",
    ]
    for k in (
        "fps_mean",
        "fps_median",
        "latency_total_ms_mean",
        "latency_total_ms_peak",
        "latency_total_ms_p99",
        "cpu_temp_mean",
        "cpu_temp_max",
        "dropped_frames_heuristic",
        "spoof_rows",
        "real_rows",
        "sim_std_mean_over_tracks",
    ):
        if k in stats and stats[k] is not None:
            lines_md.append(f"- **{k}**: `{stats[k]}`")
    lines_md.extend(
        [
            "",
            "## Plots",
            "",
            f"PNG files under `{plots_dir}` with prefix `report_{batch_ts}_`.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines_md), encoding="utf-8")

    stats["plots_directory"] = str(plots_dir)
    stats["summary_json"] = str(json_path)
    stats["summary_markdown"] = str(md_path)

    plot_count = len(list(plots_dir.glob(f"report_{batch_ts}_*.png")))
    stats["plot_files_count"] = plot_count

    _LOG.info(
        "Experiment report: %d PNG(s), summaries in %s",
        plot_count,
        summ_dir,
    )
    return stats


def main_cli() -> int:
    """CLI: python -m edge.experiment_report [EXPERIMENT_ROOT]"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EXPERIMENT_ROOT")
    if not root:
        print("Usage: python -m edge.experiment_report <EXPERIMENT_ROOT>", file=sys.stderr)
        return 1
    out = generate_experiment_report(experiment_root=root)
    print(json.dumps(out, indent=2, default=str) if out else "{}")
    return 0 if out else 2


if __name__ == "__main__":
    raise SystemExit(main_cli())
