"""
analyze_pi_perf.py
==================

Research-oriented performance analysis for the Pi 4 deployment.
Consumes data/diagnostic_log.csv (written by edge/main.py with the
Phase 5 performance-instrumentation columns) and produces:

    * FPS analysis
        - rolling FPS histogram
        - FPS time-series (frames vs time)
        - FPS box-plot per experiment_label

    * Per-stage latency
        - box-plots: t_detect_ms, t_liveness_ms, t_embed_ms, t_match_ms
        - cumulative latency distribution (CDF) for each stage

    * System resources
        - CPU temperature over time (thermal throttle detection)
        - CPU% over time
        - Memory (RSS MB) over time

    * Scaling behaviour
        - total latency vs number of active faces per frame

    * Per-experiment slicing
        - if rows carry experiment_label, all plots are re-emitted per label

Usage
-----
    python analyze_pi_perf.py
    python analyze_pi_perf.py --diag data/diagnostic_log.csv
    python analyze_pi_perf.py --out data/plots/pi_perf
    python analyze_pi_perf.py --label overhead_3m
    python analyze_pi_perf.py --per-label

Outputs PNG figures and CSV summaries under data/plots/pi_perf/.
Depends on pandas + matplotlib (already installed on the dev machine).
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from typing import Optional

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Pi thermal throttling threshold (degrees C)
THERMAL_WARN_C = 80.0


# ------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------
def load_diag(path: str, label: Optional[str] = None) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Diagnostic log not found: {path}")

    df = pd.read_csv(path)

    required_perf = {"fps_rolling", "t_detect_ms", "t_liveness_ms",
                     "t_embed_ms", "t_match_ms", "cpu_temp_c", "cpu_pct", "mem_mb"}
    missing = required_perf - set(df.columns)
    if missing:
        raise ValueError(
            f"Diagnostic log missing performance columns {sorted(missing)}. "
            f"Run the pipeline with the Phase 5 instrumented build first. "
            f"The old log will have been auto-rotated to diagnostic_log.archived_*.csv."
        )

    if label is not None:
        if "experiment_label" not in df.columns:
            raise ValueError("--label given but log has no experiment_label column")
        df = df[df["experiment_label"].astype(str) == label].copy()
        if df.empty:
            raise ValueError(f"No rows with experiment_label='{label}'")

    return df


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _save(fig: plt.Figure, out_dir: str, name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# ------------------------------------------------------------------
# FPS plots
# ------------------------------------------------------------------
def plot_fps_histogram(df: pd.DataFrame, out_dir: str) -> str:
    fps = df["fps_rolling"].dropna()
    fps = fps[fps > 0]
    if fps.empty:
        return ""
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(fps, bins=40, color="#1f77b4", edgecolor="black", alpha=0.8)
    ax.axvline(fps.median(), color="red", linestyle="--",
               label=f"median = {fps.median():.1f} fps")
    ax.axvline(fps.quantile(0.10), color="orange", linestyle=":",
               label=f"p10 = {fps.quantile(0.10):.1f} fps")
    ax.set_xlabel("Rolling FPS (30-frame window)")
    ax.set_ylabel("frame count")
    ax.set_title("FPS Distribution\n(Pi 4 deployment baseline)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "01_fps_histogram.png")


def plot_fps_timeseries(df: pd.DataFrame, out_dir: str) -> str:
    fps = df.drop_duplicates("timestamp").sort_values("timestamp")
    fps = fps[fps["fps_rolling"] > 0]
    if fps.empty:
        return ""
    t_rel = fps["timestamp"] - fps["timestamp"].iloc[0]
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(t_rel, fps["fps_rolling"], lw=0.8, alpha=0.7)
    ax.axhline(8, color="orange", linestyle=":", label="FPS target (8)")
    ax.set_xlabel("elapsed time (s)")
    ax.set_ylabel("FPS (rolling)")
    ax.set_title("FPS over Session\n(drops indicate detection latency spikes or throttling)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "02_fps_timeseries.png")


# ------------------------------------------------------------------
# Per-stage latency
# ------------------------------------------------------------------
def plot_stage_latency_boxplots(df: pd.DataFrame, out_dir: str) -> str:
    stages = {
        "t_detect_ms":   "YuNet\ndetect",
        "t_liveness_ms": "Liveness\nengine",
        "t_embed_ms":    "Align +\nEmbed",
        "t_match_ms":    "Pose\nmatch",
    }
    data = []
    labels = []
    for col, label in stages.items():
        vals = df[col].dropna()
        vals = vals[vals > 0]
        if not vals.empty:
            data.append(vals.values)
            labels.append(label)

    if not data:
        return ""

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.boxplot(data, labels=labels, showfliers=False, patch_artist=True)
    ax.axhline(65, color="red", linestyle="--", alpha=0.6,
               label="Pi 4 target latency (65 ms total)")
    ax.set_ylabel("latency (ms)")
    ax.set_title("Per-Stage Latency Distribution\n(whiskers = p5-p95, no outliers shown)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    return _save(fig, out_dir, "03_stage_latency_boxplots.png")


def plot_stage_latency_cdf(df: pd.DataFrame, out_dir: str) -> str:
    stages = {
        "t_detect_ms":   "YuNet detect",
        "t_liveness_ms": "Liveness",
        "t_embed_ms":    "Align+Embed",
        "t_match_ms":    "Pose match",
        "latency_ms":    "Total frame",
    }
    fig, ax = plt.subplots(figsize=(10, 5))
    for col, label in stages.items():
        if col not in df.columns:
            continue
        vals = df[col].dropna().sort_values()
        vals = vals[vals > 0]
        if vals.empty:
            continue
        cdf = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals.values, cdf, label=label)
    ax.axvline(65, color="black", linestyle=":", alpha=0.6,
               label="65 ms target")
    ax.set_xlabel("latency (ms)")
    ax.set_ylabel("CDF")
    ax.set_title("Cumulative Latency Distribution by Stage")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "04_stage_latency_cdf.png")


# ------------------------------------------------------------------
# System resources
# ------------------------------------------------------------------
def plot_cpu_temperature(df: pd.DataFrame, out_dir: str) -> str:
    temps = df[df["cpu_temp_c"] > 0].drop_duplicates("timestamp").sort_values("timestamp")
    if temps.empty:
        return ""
    t_rel = temps["timestamp"] - temps["timestamp"].iloc[0]
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(t_rel, temps["cpu_temp_c"], color="#d62728", lw=0.9)
    ax.axhline(THERMAL_WARN_C, color="black", linestyle="--",
               label=f"Throttle threshold ({THERMAL_WARN_C:.0f} C)")
    ax.fill_between(t_rel, THERMAL_WARN_C, temps["cpu_temp_c"],
                    where=temps["cpu_temp_c"] >= THERMAL_WARN_C,
                    alpha=0.25, color="#d62728", label="Throttled region")
    ax.set_xlabel("elapsed time (s)")
    ax.set_ylabel("CPU temperature (C)")
    ax.set_title("CPU Temperature over Session\n(>80 C = thermal throttle active)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "05_cpu_temperature.png")


def plot_cpu_memory(df: pd.DataFrame, out_dir: str) -> str:
    sub = df[df["cpu_pct"] > 0].drop_duplicates("timestamp").sort_values("timestamp")
    if sub.empty:
        return ""
    t_rel = sub["timestamp"] - sub["timestamp"].iloc[0]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)

    ax1.plot(t_rel, sub["cpu_pct"], color="#ff7f0e")
    ax1.set_ylabel("CPU usage (%)")
    ax1.set_title("CPU % and Memory over Session")
    ax1.set_ylim(0, 110)
    ax1.grid(True, alpha=0.3)

    ax2.plot(t_rel, sub["mem_mb"], color="#2ca02c")
    ax2.set_xlabel("elapsed time (s)")
    ax2.set_ylabel("RSS memory (MB)")
    ax2.grid(True, alpha=0.3)

    return _save(fig, out_dir, "06_cpu_memory.png")


# ------------------------------------------------------------------
# Scaling: latency vs active faces per frame
# ------------------------------------------------------------------
def plot_latency_vs_face_count(df: pd.DataFrame, out_dir: str) -> str:
    if "latency_ms" not in df.columns:
        return ""
    # Proxy for face count: number of rows with the same timestamp
    face_count = (
        df.groupby("timestamp")["track_id"]
          .nunique()
          .reset_index(name="face_count")
    )
    merged = df.merge(face_count, on="timestamp", how="left")
    merged = merged[merged["latency_ms"] > 0]
    if merged.empty:
        return ""

    grouped = merged.groupby("face_count")["latency_ms"].agg(["mean", "std", "count"])
    grouped = grouped[grouped["count"] >= 5]
    if grouped.empty:
        return ""

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.errorbar(grouped.index, grouped["mean"], yerr=grouped["std"],
                marker="o", capsize=4, color="#1f77b4")
    ax.axhline(65, color="red", linestyle="--", alpha=0.6,
               label="65 ms target")
    ax.set_xlabel("active faces in frame")
    ax.set_ylabel("mean total latency (ms) +/- 1 std")
    ax.set_title("Latency vs Face Count\n(pipeline scaling behaviour)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "07_latency_vs_face_count.png")


# ------------------------------------------------------------------
# Summary tables
# ------------------------------------------------------------------
def write_summary_csvs(df: pd.DataFrame, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    # Stage latency statistics
    stage_cols = ["t_detect_ms", "t_liveness_ms", "t_embed_ms", "t_match_ms", "latency_ms"]
    present    = [c for c in stage_cols if c in df.columns]
    summary = df[present].describe(percentiles=[0.10, 0.50, 0.90])
    summary.to_csv(os.path.join(out_dir, "summary_latency.csv"))

    # FPS statistics
    fps_stats = df["fps_rolling"].dropna()
    fps_stats = fps_stats[fps_stats > 0].describe(percentiles=[0.05, 0.50, 0.95])
    fps_stats.to_csv(os.path.join(out_dir, "summary_fps.csv"))

    # Thermal summary
    temp_col = df[df["cpu_temp_c"] > 0]["cpu_temp_c"]
    if not temp_col.empty:
        with open(os.path.join(out_dir, "summary_thermal.txt"), "w") as f:
            f.write("CPU TEMPERATURE SUMMARY\n")
            f.write("=" * 40 + "\n")
            f.write(f"  mean    : {temp_col.mean():.1f} C\n")
            f.write(f"  max     : {temp_col.max():.1f} C\n")
            f.write(f"  p90     : {temp_col.quantile(0.90):.1f} C\n")
            throttled = (temp_col >= THERMAL_WARN_C).mean() * 100
            f.write(f"  throttled fraction: {throttled:.1f}%\n")
            if throttled > 5:
                f.write("\n  WARNING: sustained throttling detected.\n")
                f.write("  Add a heatsink/fan and re-run the soak test.\n")


# ------------------------------------------------------------------
# Console summary
# ------------------------------------------------------------------
def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 64)
    print("Pi 4 PERFORMANCE REPORT")
    print("=" * 64)
    print(f"\nTotal diagnostic rows: {len(df)}")
    fps = df["fps_rolling"].dropna()
    fps = fps[fps > 0]
    if not fps.empty:
        print(f"\n[ FPS ]")
        print(f"  median : {fps.median():.1f}")
        print(f"  p10    : {fps.quantile(0.10):.1f}")
        print(f"  p90    : {fps.quantile(0.90):.1f}")
    for col, label in [
        ("t_detect_ms",   "YuNet detect"),
        ("t_liveness_ms", "Liveness"),
        ("t_embed_ms",    "Align+Embed"),
        ("t_match_ms",    "Pose match"),
        ("latency_ms",    "Total frame"),
    ]:
        if col not in df.columns:
            continue
        vals = df[col].dropna()
        vals = vals[vals > 0]
        if vals.empty:
            continue
        print(f"\n[ {label} latency (ms) ]")
        print(f"  median : {vals.median():.1f}")
        print(f"  p90    : {vals.quantile(0.90):.1f}")
        print(f"  max    : {vals.max():.1f}")
    temp = df[df["cpu_temp_c"] > 0]["cpu_temp_c"]
    if not temp.empty:
        print(f"\n[ CPU temperature (C) ]")
        print(f"  mean : {temp.mean():.1f}")
        print(f"  max  : {temp.max():.1f}")
        if temp.max() >= THERMAL_WARN_C:
            print(f"  WARNING: temperature reached {temp.max():.1f} C "
                  f"(throttle threshold {THERMAL_WARN_C:.0f} C)")
    print()


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--diag",      default="data/diagnostic_log.csv",
                        help="path to diagnostic_log.csv")
    parser.add_argument("--out",       default="data/plots/pi_perf",
                        help="output directory for plots + summaries")
    parser.add_argument("--label",     default=None,
                        help="restrict analysis to a single experiment_label")
    parser.add_argument("--per-label", action="store_true",
                        help="also emit per-experiment_label sub-reports")
    args = parser.parse_args()

    df = load_diag(args.diag, label=args.label)

    out_dir = args.out
    if args.label:
        out_dir = os.path.join(out_dir, f"label_{args.label}")

    plots = [
        plot_fps_histogram(df, out_dir),
        plot_fps_timeseries(df, out_dir),
        plot_stage_latency_boxplots(df, out_dir),
        plot_stage_latency_cdf(df, out_dir),
        plot_cpu_temperature(df, out_dir),
        plot_cpu_memory(df, out_dir),
        plot_latency_vs_face_count(df, out_dir),
    ]
    write_summary_csvs(df, out_dir)
    print_summary(df)

    print(f"Outputs written to: {os.path.abspath(out_dir)}")
    for p in plots:
        if p:
            print(f"  - {os.path.basename(p)}")

    if args.per_label and "experiment_label" in df.columns and args.label is None:
        labels = [l for l in df["experiment_label"].astype(str).unique() if l]
        for label in labels:
            sub = df[df["experiment_label"].astype(str) == label]
            if len(sub) < 30:
                continue
            sub_dir = os.path.join(args.out, f"label_{label}")
            for fn in (plot_fps_histogram, plot_fps_timeseries,
                       plot_stage_latency_boxplots, plot_stage_latency_cdf,
                       plot_cpu_temperature, plot_cpu_memory,
                       plot_latency_vs_face_count):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    fn(sub, sub_dir)
            write_summary_csvs(sub, sub_dir)
            print(f"  -> per-label report: {sub_dir}")


if __name__ == "__main__":
    main()
