# edge/experiment_report.py
"""
Post-run experiment report: load session CSVs, metadata, and logs;
write PNG plots to experiments/<id>/plots/ and JSON+MD summaries to summaries/.

Uses matplotlib Agg backend (headless-safe). Optional imports: if pandas/matplotlib
are missing, generation is skipped with a log line (pipeline stays healthy).

Generates relational (hypothesis-oriented) figures alongside legacy performance
curves: PAD geometry vs confidence, recognition vs blur, hybrid edge–cloud
comparisons, latency composition, and threshold dynamics.
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


def _experiment_label_from_diag(diag: Any) -> str:
    if diag is None or len(diag) == 0:
        return ""
    if "experiment_label" not in diag.columns:
        return ""
    v = diag["experiment_label"].dropna().astype(str).str.strip()
    v = v[v != ""]
    if len(v) == 0:
        return ""
    u = v.unique()
    if len(u) == 1:
        return str(u[0])
    return "multi:" + ",".join(str(x) for x in u[:4]) + ("…" if len(u) > 4 else "")


def _annotate_figure(fig: Any, exp_lbl: str, batch_ts: str) -> None:
    parts = []
    if exp_lbl:
        parts.append(f"experiment_label={exp_lbl}")
    parts.append(f"generated_utc={batch_ts}")
    fig.text(0.5, 0.01, " | ".join(parts), ha="center", fontsize=8, color="#444444")


def _boolish_series(s: Any) -> Any:
    """Normalize CSV edge_cloud_agree values to nullable boolean."""
    import pandas as pd

    if s is None or len(s) == 0:
        return s
    out = []
    for v in s:
        if pd.isna(v):
            out.append(np.nan)
            continue
        if isinstance(v, (bool, np.bool_)):
            out.append(bool(v))
            continue
        t = str(v).strip().lower()
        if t in ("true", "1", "yes"):
            out.append(True)
        elif t in ("false", "0", "no"):
            out.append(False)
        else:
            out.append(np.nan)
    return pd.Series(out, index=s.index)


def _merge_telemetry_cloud_rtt(tel: Any, diag: Any, pd: Any) -> Any:
    """Attach mean cloud RTT (seconds bucket) to telemetry rows via merge_asof."""
    d = diag[
        diag["cloud_rtt_ms"].notna() & (diag["cloud_rtt_ms"].astype(float) > 0)
    ][["timestamp", "cloud_rtt_ms"]].copy()
    if len(d) == 0:
        tel = tel.copy()
        tel["cloud_rtt_ms_merged"] = np.nan
        return tel
    d = d.groupby("timestamp", as_index=False)["cloud_rtt_ms"].mean()
    d = d.sort_values("timestamp")
    left = tel.sort_values("timestamp").copy()
    merged = pd.merge_asof(
        left,
        d.rename(columns={"cloud_rtt_ms": "cloud_rtt_ms_merged"}),
        on="timestamp",
        direction="nearest",
        tolerance=0.25,
    )
    return merged


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

    exp_lbl = _experiment_label_from_diag(diag)
    stats["experiment_label"] = exp_lbl or None

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

        # Performance plots (Track 1)
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["fps_rolling"], lw=0.8, color="#1f77b4")
        _style_axes(ax, "Time (s from start)", "Rolling FPS", "FPS vs time")
        _annotate_figure(fig, exp_lbl, batch_ts)
        _savefig(plots_dir / f"report_{batch_ts}_perf_fps.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["t_total_ms"], lw=0.7, color="#d62728", alpha=0.85)
        _style_axes(ax, "Time (s)", "Total frame latency (ms)", "Pipeline latency vs time")
        _annotate_figure(fig, exp_lbl, batch_ts)
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
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / f"report_{batch_ts}_perf_{slug}.png", plt)

        # (10) FPS vs CPU temperature — deployment stress hypothesis
        if "cpu_temp_c" in tel.columns:
            tt = tel[(tel["cpu_temp_c"].astype(float) > 0)].copy()
            if len(tt) > 1:
                fig, ax = plt.subplots(figsize=(8, 5))
                sc = ax.scatter(
                    tt["cpu_temp_c"],
                    tt["fps_rolling"],
                    c=tt["_t_rel_s"],
                    cmap="viridis",
                    s=12,
                    alpha=0.65,
                )
                plt.colorbar(sc, ax=ax, label="Time (s)")
                _style_axes(
                    ax,
                    "CPU / SoC temperature (°C)",
                    "Rolling FPS",
                    "Thermal headroom vs throughput (color = session time)",
                )
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / f"report_{batch_ts}_hybrid_fps_vs_temp.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["cpu_pct"], lw=0.7, label="CPU %", color="#2ca02c")
        _style_axes(ax, "Time (s)", "CPU %", "CPU usage vs time")
        _annotate_figure(fig, exp_lbl, batch_ts)
        _savefig(plots_dir / f"report_{batch_ts}_perf_cpu.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["mem_mb"], lw=0.7, color="#9467bd")
        _style_axes(ax, "Time (s)", "RSS (MB)", "Memory usage vs time")
        _annotate_figure(fig, exp_lbl, batch_ts)
        _savefig(plots_dir / f"report_{batch_ts}_perf_mem.png", plt)

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(tel["_t_rel_s"], tel["cpu_temp_c"], lw=0.7, color="#ff7f0e")
        _style_axes(ax, "Time (s)", "Temperature (°C)", "SoC temperature vs time")
        _annotate_figure(fig, exp_lbl, batch_ts)
        _savefig(plots_dir / f"report_{batch_ts}_perf_temp.png", plt)

        # (5) Latency composition stack (frame telemetry)
        stack_cols = [
            ("t_detect_ms", "Detect"),
            ("t_liveness_max_ms", "Liveness"),
            ("t_embed_max_ms", "Embed"),
            ("t_match_max_ms", "Match"),
        ]
        present = [(c, lb) for c, lb in stack_cols if c in tel.columns]
        if len(present) >= 2:
            tel_s = tel.copy()
            if diag is not None and len(diag) > 0 and "cloud_rtt_ms" in diag.columns:
                tel_s = _merge_telemetry_cloud_rtt(tel_s, diag, pd)
                if "cloud_rtt_ms_merged" in tel_s.columns:
                    present.append(("cloud_rtt_ms_merged", "Cloud RTT"))

            mat = np.maximum(
                0.0,
                np.column_stack(
                    [tel_s[c].astype(float).fillna(0.0).to_numpy() for c, _ in present]
                ),
            )
            fig, ax = plt.subplots(figsize=(9, 4.5))
            ax.stackplot(
                tel_s["_t_rel_s"],
                mat.T,
                labels=[lb for _, lb in present],
                colors=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"][: mat.shape[1]],
                alpha=0.85,
            )
            ax.legend(loc="upper left", fontsize=8)
            _style_axes(
                ax,
                "Time (s from start)",
                "Stacked time (ms)",
                "Latency composition: detect → liveness → embed → match (+ cloud when present)",
            )
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_hybrid_latency_stack.png", plt)

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

        if "fan_state" in tel.columns:
            fs = tel["fan_state"].astype(str).str.strip()
            fs = fs[fs != ""]
            if len(fs) > 0:
                stats["fan_state_last"] = str(fs.iloc[-1])
                stats["fan_state_counts"] = {
                    str(k): int(v) for k, v in fs.value_counts().items()
                }

            _fan_levels = {"OFF": 0, "LOW": 1, "HIGH": 2, "MAX": 3}
            tel_fan = tel.copy()
            tel_fan["_fan_level"] = (
                tel_fan["fan_state"].astype(str).str.upper().map(_fan_levels)
            )
            valid_fan = tel_fan["_fan_level"].notna()

            if valid_fan.any() and (tel_fan["cpu_temp_c"].astype(float) > 0).any():
                fig, ax = plt.subplots(figsize=(9, 4))
                ax.plot(
                    tel_fan["_t_rel_s"],
                    tel_fan["cpu_temp_c"],
                    lw=0.8,
                    color="#ff7f0e",
                    label="CPU temp (°C)",
                )
                ax2 = ax.twinx()
                ax2.step(
                    tel_fan["_t_rel_s"],
                    tel_fan["_fan_level"].fillna(0),
                    where="post",
                    lw=0.9,
                    color="#1f77b4",
                    alpha=0.75,
                    label="Fan level",
                )
                ax2.set_yticks([0, 1, 2, 3])
                ax2.set_yticklabels(["OFF", "LOW", "HIGH", "MAX"])
                ax2.set_ylabel("Fan state")
                _style_axes(ax, "Time (s from start)", "Temperature (°C)", "Fan state vs CPU temperature")
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / "fan_vs_temp.png", plt)

            tt_lat = tel_fan[
                (tel_fan["cpu_temp_c"].astype(float) > 0) & tel_fan["t_total_ms"].notna()
            ]
            if len(tt_lat) > 1:
                fig, ax = plt.subplots(figsize=(8, 5))
                ax.scatter(
                    tt_lat["cpu_temp_c"],
                    tt_lat["t_total_ms"],
                    c=tt_lat["_t_rel_s"],
                    cmap="plasma",
                    s=12,
                    alpha=0.65,
                )
                _style_axes(
                    ax,
                    "CPU temperature (°C)",
                    "Total frame latency (ms)",
                    "Temperature vs pipeline latency",
                )
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / "temp_vs_latency.png", plt)

            tt_fps = tel_fan[
                valid_fan & (tel_fan["fps_rolling"].astype(float) > 0)
            ]
            if len(tt_fps) > 1:
                fig, ax = plt.subplots(figsize=(8, 5))
                ax.scatter(
                    tt_fps["_fan_level"],
                    tt_fps["fps_rolling"],
                    c=tt_fps["_t_rel_s"],
                    cmap="viridis",
                    s=14,
                    alpha=0.65,
                )
                ax.set_xticks([0, 1, 2, 3])
                ax.set_xticklabels(["OFF", "LOW", "HIGH", "MAX"])
                _style_axes(ax, "Fan state", "Rolling FPS", "Fan state vs throughput")
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / "fan_vs_fps.png", plt)

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

        # Orientation activation (explicit column or legacy inference)
        if "orientation_active" in diag.columns:
            oa = diag["orientation_active"].astype(int)
            stats["orientation_active_fraction_rows"] = float(oa.mean())
            stats["orientation_active_count"] = int((oa == 1).sum())
        else:
            legacy_active = (diag["orient_ratio"].astype(float) > 0) & (
                diag["mode_raw"].astype(str).str.upper() != "NA"
            )
            stats["orientation_active_fraction_rows"] = float(legacy_active.mean())
            stats["orientation_active_count"] = int(legacy_active.sum())

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
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_pad_live_conf.png", plt)

        if "rigid_ratio" in diag.columns:
            g2 = diag.groupby("timestamp", as_index=False)["rigid_ratio"].mean()
            g2["_t_rel_s"] = g2["timestamp"].astype(float) - float(diag["timestamp"].iloc[0])
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(g2["_t_rel_s"], g2["rigid_ratio"], lw=0.8, color="#8c564b")
            _style_axes(ax, "Time (s)", "Mean rigid_ratio", "Rigid ratio over time")
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_pad_rigid_ratio.png", plt)

        if "lbl" in diag.columns:
            vc = diag["lbl"].value_counts()
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.bar(vc.index.astype(str), vc.values, color="#bcbd22", edgecolor="black")
            _style_axes(ax, "Label", "Count", "Liveness label distribution")
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_pad_label_dist.png", plt)
            stats["spoof_rows"] = int((diag["lbl"] == "SPOOF").sum())
            stats["real_rows"] = int((diag["lbl"] == "REAL").sum())

        # (1)(2) PAD geometry vs confidence — REAL vs SPOOF
        if (
            "orient_ratio" in diag.columns
            and "live_conf" in diag.columns
            and "lbl" in diag.columns
        ):
            fig, ax = plt.subplots(figsize=(8, 5))
            oplot = diag
            if "orientation_active" in diag.columns:
                oa = diag[diag["orientation_active"].astype(int) == 1]
                if len(oa) > 0:
                    oplot = oa
            for lbl, color, m in (
                ("REAL", "#2ca02c", "o"),
                ("SPOOF", "#d62728", "s"),
            ):
                sub = oplot[oplot["lbl"] == lbl].copy()
                sub = sub[sub["orient_ratio"].astype(float) > 0]
                if len(sub) > 0:
                    ax.scatter(
                        sub["orient_ratio"],
                        sub["live_conf"],
                        s=14,
                        alpha=0.35,
                        c=color,
                        marker=m,
                        label=f"{lbl} (n={len(sub)})",
                    )
            ax.legend()
            _style_axes(
                ax,
                "orient_ratio (geometry)",
                "Liveness confidence",
                "PAD geometry vs liveness confidence — does gaze/pose explain scores?",
            )
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_rel_orient_vs_live_conf.png", plt)

        if "rigid_ratio" in diag.columns and "live_conf" in diag.columns and "lbl" in diag.columns:
            fig, ax = plt.subplots(figsize=(8, 5))
            for lbl, color, m in (
                ("REAL", "#2ca02c", "o"),
                ("SPOOF", "#d62728", "s"),
            ):
                sub = diag[(diag["lbl"] == lbl) & (diag["rigid_ratio"].notna())]
                if len(sub) > 0:
                    ax.scatter(
                        sub["rigid_ratio"],
                        sub["live_conf"],
                        s=14,
                        alpha=0.35,
                        c=color,
                        marker=m,
                        label=f"{lbl} (n={len(sub)})",
                    )
            ax.legend()
            _style_axes(
                ax,
                "rigid_ratio (planar motion evidence)",
                "Liveness confidence",
                "Rigid structure vs liveness — why planar attacks fail or leak through",
            )
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_rel_rigid_vs_live_conf.png", plt)

        # (3) Blur (Laplacian var) vs similarity
        if "avg_blur" in diag.columns and "sim" in diag.columns:
            sub = diag[(diag["avg_blur"].astype(float) > 0) & (diag["sim"].notna())].copy()
            if len(sub) > 10:
                fig, ax = plt.subplots(figsize=(8, 5))
                if "lbl" in sub.columns:
                    for lbl, color in (("REAL", "#2ca02c"), ("SPOOF", "#d62728")):
                        ss = sub[sub["lbl"] == lbl]
                        if len(ss) > 0:
                            ax.scatter(
                                ss["avg_blur"],
                                ss["sim"],
                                s=12,
                                alpha=0.4,
                                c=color,
                                label=f"{lbl} (n={len(ss)})",
                            )
                    ax.legend()
                else:
                    ax.scatter(sub["avg_blur"], sub["sim"], s=12, alpha=0.4, c="#555555")
                _style_axes(
                    ax,
                    "avg_blur (Laplacian variance, face crop)",
                    "Embedding similarity",
                    "Image sharpness vs recognition quality",
                )
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / f"report_{batch_ts}_rel_blur_vs_similarity.png", plt)

        if "sim" in diag.columns and "lbl" in diag.columns:
            sreal = diag.loc[diag["lbl"] == "REAL", "sim"].dropna()
            sspoof = diag.loc[diag["lbl"] == "SPOOF", "sim"].dropna()
            fig, ax = plt.subplots(figsize=(8, 4))
            if len(sreal):
                ax.hist(sreal, bins=40, alpha=0.6, label="REAL", color="green", density=True)
            if len(sspoof):
                ax.hist(sspoof, bins=40, alpha=0.6, label="SPOOF", color="red", density=True)
            ax.legend()
            _style_axes(ax, "Similarity", "Density", "Similarity distribution (REAL vs SPOOF)")
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_rec_sim_by_label.png", plt)

        if "sim" in diag.columns:
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.hist(diag["sim"].dropna(), bins=50, color="#7f7f7f", edgecolor="white")
            _style_axes(ax, "Similarity", "Count", "Similarity histogram (all rows)")
            _annotate_figure(fig, exp_lbl, batch_ts)
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
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_rec_threshold_scatter.png", plt)

        # (4) Similarity stability over time — per track (REAL)
        if "sim" in diag.columns and "track_id" in diag.columns and "lbl" in diag.columns:
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
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / f"report_{batch_ts}_rec_conf_stability.png", plt)
                stats["sim_std_mean_over_tracks"] = float(std_by.mean()) if len(std_by) else None
                # Mean absolute step size (jitter) within track
                jit = []
                for _, gg in real.groupby("track_id"):
                    gg = gg.sort_values("timestamp")
                    if len(gg) > 2:
                        jit.append(float(gg["sim"].diff().abs().mean()))
                stats["recognition_mean_abs_sim_step"] = (
                    float(np.mean(jit)) if jit else None
                )
                if jit:
                    fig, ax = plt.subplots(figsize=(8, 4))
                    ax.hist(jit, bins=min(40, max(5, len(jit))), color="#c49c94", edgecolor="white")
                    _style_axes(
                        ax,
                        "Mean |Δsim| within track",
                        "Track count",
                        "Recognition jitter distribution (REAL tracks)",
                    )
                    _annotate_figure(fig, exp_lbl, batch_ts)
                    _savefig(plots_dir / f"report_{batch_ts}_rel_recognition_jitter_hist.png", plt)
            else:
                stats["sim_std_mean_over_tracks"] = None
                stats["recognition_mean_abs_sim_step"] = None

            # Lines: up to 6 busiest tracks (REAL + BUFFERING/MATCHED useful)
            if "identity" in diag.columns:
                traj = diag[diag["identity"].notna() & (diag["identity"].astype(str) != "NA")]
                traj = traj[traj["identity"].astype(str) != "UNKNOWN"]
            else:
                traj = diag.iloc[:0]
            if len(traj) > 20:
                top_ids = (
                    traj.groupby("track_id").size().sort_values(ascending=False).head(6).index
                )
                fig, ax = plt.subplots(figsize=(10, 5))
                for tid in top_ids:
                    g = traj[traj["track_id"] == tid].sort_values("timestamp")
                    ax.plot(g["_t_rel_s"], g["sim"], lw=1.0, alpha=0.75, label=f"id {tid}")
                ax.legend(fontsize=7, ncol=3)
                _style_axes(
                    ax,
                    "Time (s)",
                    "Similarity",
                    "Similarity trajectories (selected tracks)",
                )
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / f"report_{batch_ts}_rel_sim_vs_time_by_track.png", plt)

        # (11)(12) Threshold crossing & oscillation
        crossings = 0
        if (
            "sim" in diag.columns
            and "th_high" in diag.columns
            and "track_id" in diag.columns
        ):
            rec = diag[diag["sim"].notna() & diag["th_high"].notna()].copy()
            for _, gg in rec.groupby("track_id"):
                gg = gg.sort_values("timestamp")
                above = (gg["sim"].astype(float).to_numpy() >= gg["th_high"].astype(float).to_numpy())
                if len(above) > 1:
                    crossings += int(np.sum(above[1:] != above[:-1]))
            stats["threshold_crossings_total"] = int(crossings)

            sample_tid = None
            if rec["track_id"].nunique() >= 1:
                sample_tid = rec.groupby("track_id").size().idxmax()
            if sample_tid is not None:
                g = rec[rec["track_id"] == sample_tid].sort_values("timestamp")
                if len(g) > 5:
                    fig, ax = plt.subplots(figsize=(10, 4))
                    ax.plot(g["_t_rel_s"], g["sim"], lw=1.2, label="sim", color="#1f77b4")
                    ax.plot(
                        g["_t_rel_s"],
                        g["th_high"],
                        lw=1.0,
                        ls="--",
                        color="#d62728",
                        label="th_high",
                    )
                    cross = (
                        g["sim"].astype(float).to_numpy()
                        >= g["th_high"].astype(float).to_numpy()
                    )
                    mark = np.zeros(len(cross), dtype=bool)
                    if len(cross) > 1:
                        mark[1:] = cross[1:] != cross[:-1]
                    if mark.any():
                        ax.scatter(
                            g["_t_rel_s"].to_numpy()[mark],
                            g["sim"].to_numpy()[mark],
                            c="orange",
                            s=40,
                            zorder=5,
                            label="crossing",
                        )
                    ax.legend()
                    _style_axes(
                        ax,
                        "Time (s)",
                        "Similarity / threshold",
                        f"Threshold dynamics (richest track_id={sample_tid})",
                    )
                    _annotate_figure(fig, exp_lbl, batch_ts)
                    _savefig(
                        plots_dir / f"report_{batch_ts}_rel_threshold_oscillation.png",
                        plt,
                    )

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
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_pad_g_score_dist.png", plt)

        # --- Track 2 hybrid ---
        offload_n = int((diag["decision"].astype(str) == "OFFLOAD_TO_CLOUD").sum())
        stats["offload_rows"] = offload_n
        stats["offload_rate_fraction"] = float(offload_n / max(1, len(diag)))

        cloud_sub = diag[diag["cloud_rtt_ms"].notna()].copy()
        if len(cloud_sub) > 0:
            stats["cloud_rtt_ms_mean"] = float(
                cloud_sub["cloud_rtt_ms"].astype(float).mean()
            )
            stats["cloud_rtt_ms_median"] = float(
                cloud_sub["cloud_rtt_ms"].astype(float).median()
            )
        else:
            stats["cloud_rtt_ms_mean"] = None
            stats["cloud_rtt_ms_median"] = None

        agree_s = None
        if "edge_cloud_agree" in diag.columns:
            agree_s = _boolish_series(diag["edge_cloud_agree"])
            valid = agree_s.notna()
            if valid.any():
                stats["edge_cloud_agreement_rate"] = float(agree_s[valid].mean())
                stats["edge_cloud_agreement_n"] = int(valid.sum())
            else:
                stats["edge_cloud_agreement_rate"] = None
                stats["edge_cloud_agreement_n"] = 0

        # (6) Edge vs cloud confidence
        if "sim" in diag.columns and "cloud_arcface_confidence" in diag.columns:
            hy = diag[diag["cloud_arcface_confidence"].notna()].copy()
            if len(hy) > 0:
                fig, ax = plt.subplots(figsize=(7, 6))
                ax.scatter(
                    hy["sim"].astype(float),
                    hy["cloud_arcface_confidence"].astype(float),
                    c="#6baed6",
                    s=22,
                    alpha=0.5,
                )
                lim = [0, 1.05]
                ax.plot(lim, lim, "k--", lw=0.8, alpha=0.5, label="y = x")
                ax.set_xlim(lim)
                ax.set_ylim(lim)
                ax.legend()
                _style_axes(
                    ax,
                    "Edge similarity (cosine)",
                    "Cloud ArcFace confidence",
                    "Hybrid verification: edge vs cloud scores",
                )
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / f"report_{batch_ts}_hybrid_edge_vs_cloud_conf.png", plt)

        # (7) Agreement distribution
        if agree_s is not None and agree_s.notna().any():
            fig, ax = plt.subplots(figsize=(6, 4))
            vc_a = agree_s.dropna().astype(bool).value_counts()
            ax.bar(
                [str(k) for k in vc_a.index],
                vc_a.values,
                color=["#d62728", "#2ca02c"][: len(vc_a)],
                edgecolor="black",
            )
            _style_axes(
                ax,
                "edge_cloud_agree",
                "Count",
                "Edge vs cloud identity agreement (verified offloads)",
            )
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_hybrid_agreement_dist.png", plt)

        # (8) Offload frequency over time
        if "decision" in diag.columns:
            diag["__off"] = (diag["decision"].astype(str) == "OFFLOAD_TO_CLOUD").astype(float)
            win = min(101, max(11, len(diag) // 20 | 1))
            diag["__off_roll"] = diag["__off"].rolling(win, min_periods=3).mean()
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(
                diag["_t_rel_s"],
                diag["__off_roll"],
                lw=1.0,
                color="#9c9ede",
            )
            _style_axes(
                ax,
                "Time (s)",
                f"Rolling offload rate (window≈{win} rows)",
                "Hybrid routing: when edge requests cloud verification",
            )
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_hybrid_offload_rate_time.png", plt)

        # (9) Cloud RTT vs end-to-end row latency
        if "cloud_rtt_ms" in diag.columns and "latency_ms" in diag.columns:
            cc = diag[
                diag["cloud_rtt_ms"].notna() & diag["latency_ms"].notna()
            ].copy()
            if len(cc) > 3:
                fig, ax = plt.subplots(figsize=(7, 6))
                ax.scatter(
                    cc["latency_ms"].astype(float),
                    cc["cloud_rtt_ms"].astype(float),
                    s=20,
                    alpha=0.45,
                    c="#8c564b",
                )
                _style_axes(
                    ax,
                    "Per-row pipeline latency (ms)",
                    "Cloud RTT (ms)",
                    "Network cost vs local frame cost (offload rows)",
                )
                _annotate_figure(fig, exp_lbl, batch_ts)
                _savefig(plots_dir / f"report_{batch_ts}_hybrid_cloud_rtt_vs_latency.png", plt)

        # Orientation activation sanity (explicit flag)
        if "orientation_active" in diag.columns:
            fig, ax = plt.subplots(figsize=(7, 3.5))
            vc_o = diag["orientation_active"].astype(int).value_counts().sort_index()
            ax.bar([str(i) for i in vc_o.index], vc_o.values, color="#aec7e8", ec="black")
            _style_axes(
                ax,
                "orientation_active",
                "Diagnostic rows",
                "Pose estimator execution (telemetry-only flag)",
            )
            _annotate_figure(fig, exp_lbl, batch_ts)
            _savefig(plots_dir / f"report_{batch_ts}_rel_orientation_active_dist.png", plt)

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
        f"- Experiment label: **{stats.get('experiment_label') or '—'}**",
        "",
        "## Summary",
        "",
        "### Performance & deployment",
    ]
    for k in (
        "fps_mean",
        "fps_median",
        "latency_total_ms_mean",
        "latency_total_ms_peak",
        "latency_total_ms_p99",
        "cpu_temp_mean",
        "cpu_temp_max",
        "fan_state_last",
        "fan_state_counts",
        "dropped_frames_heuristic",
        "spoof_rows",
        "real_rows",
        "sim_std_mean_over_tracks",
        "recognition_mean_abs_sim_step",
    ):
        if k in stats and stats[k] is not None:
            lines_md.append(f"- **{k}**: `{stats[k]}`")

    lines_md.extend(["", "### Orientation telemetry", ""])
    for k in (
        "orientation_active_fraction_rows",
        "orientation_active_count",
    ):
        if k in stats and stats[k] is not None:
            lines_md.append(f"- **{k}**: `{stats[k]}`")

    lines_md.extend(["", "### Hybrid edge–cloud", ""])
    for k in (
        "offload_rows",
        "offload_rate_fraction",
        "cloud_rtt_ms_mean",
        "cloud_rtt_ms_median",
        "edge_cloud_agreement_rate",
        "edge_cloud_agreement_n",
    ):
        if k in stats and stats[k] is not None:
            lines_md.append(f"- **{k}**: `{stats[k]}`")

    lines_md.extend(["", "### Recognition dynamics", ""])
    for k in ("threshold_crossings_total",):
        if k in stats and stats[k] is not None:
            lines_md.append(f"- **{k}**: `{stats[k]}`")

    lines_md.extend(
        [
            "",
            "## Plots",
            "",
            f"PNG files under `{plots_dir}` with prefix `report_{batch_ts}_`.",
            "",
            "Thermal fan plots (when `fan_state` telemetry present): "
            "`fan_vs_temp.png`, `temp_vs_latency.png`, `fan_vs_fps.png`.",
            "",
            "Relational / hypothesis-driven figures use the `rel_` and `hybrid_` prefixes.",
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
