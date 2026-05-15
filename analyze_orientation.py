"""
analyze_orientation.py
======================

Research-oriented evaluation of the orientation subsystem in
edge/orientation.py. Consumes data/diagnostic_log.csv (instrumented in
this phase) and produces:

    * Threshold validation
        - histogram of orient_ratio overall
        - histogram of orient_ratio split by mode_raw
        - vertical lines at the configured OVERHEAD_TH / TILTED_TH so you
          can eyeball whether the cuts sit in natural valleys
    * Calibration suggestions
        - percentile-based recommendations for OVERHEAD_TH / TILTED_TH
          that respect the observed empirical distribution
    * Stability validation
        - per-track raw-vs-smoothed mode flip rate
        - histogram of how often mode_raw changes between consecutive
          frames within a track (overhead-classroom geometry stress)
    * Recognition vs orientation
        - sim-vs-ratio scatter, coloured by decision
        - mean similarity binned by ratio
        - decision-rate breakdown per pose mode
        - sim-vs-distance, faceted by pose mode
    * Per-experiment slicing
        - if rows carry an experiment_label, all of the above are also
          re-emitted per label so frontal/overhead/tilted/distance
          captures stay separable in the paper

Usage
-----
    python analyze_orientation.py
    python analyze_orientation.py --diag data/diagnostic_log.csv
    python analyze_orientation.py --out data/plots/orientation
    python analyze_orientation.py --label overhead_3m   # single slice

Outputs PNG figures and CSV summaries under data/plots/orientation/.
Lightweight — only depends on pandas + matplotlib (already installed).
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from typing import Optional

import numpy as np
import pandas as pd

# Headless backend so this works over SSH on the Pi without a display.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# We import the runtime thresholds from settings so plots stay aligned
# with whatever values are currently active. This keeps the analysis a
# faithful reflection of the deployed classifier rather than assuming
# the legacy 0.6 / 0.9 cuts.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from config import settings  # type: ignore
    OVERHEAD_TH = float(settings.ORIENTATION_OVERHEAD_TH)
    TILTED_TH = float(settings.ORIENTATION_TILTED_TH)
except Exception:
    # Fallback to the original hard-coded defaults if settings can't
    # be imported (e.g. running this script in a stripped environment).
    OVERHEAD_TH, TILTED_TH = 0.60, 0.90

MODE_COLORS = {
    "FRONTAL": "#1f77b4",
    "TILTED": "#ff7f0e",
    "OVERHEAD": "#d62728",
    "NA": "#7f7f7f",
}

def _print_orientation_gating_report(df: pd.DataFrame) -> None:
    """
    Summarise rows where pose telemetry was never attached (orient_ratio==0 /
    mode_raw NA) vs. the pipeline decision — usually tracker vs detector IoU
    drift or early continue before estimate_mode().
    Prefer explicit ``orientation_active`` when the diagnostic schema includes it.
    """
    n = len(df)
    if n == 0:
        print("\n[orientation gating] No rows in slice.\n")
        return
    if "orientation_active" in df.columns:
        active = df["orientation_active"].astype(int) == 1
        inactive = ~active
        print("\n--- Orientation telemetry gating (full diagnostic slice) ---")
        print(f"Rows: {n}")
        print(
            f"  orientation_active=0: {int(inactive.sum())} "
            f"({100.0 * inactive.mean():.1f}%)"
        )
        print(
            f"  orientation_active=1 (pose ran): {int(active.sum())} "
            f"({100.0 * active.mean():.1f}%)"
        )
    else:
        na_raw = df["mode_raw"].astype(str).str.upper().eq("NA")
        zero_ratio = df["orient_ratio"] <= 0
        inactive = zero_ratio & na_raw
        active = ~(zero_ratio & na_raw)
        print("\n--- Orientation telemetry gating (full diagnostic slice) ---")
        print(f"Rows: {n}")
        print(
            f"  Inactive (orient_ratio<=0 & mode_raw==NA): {int(inactive.sum())} "
            f"({100.0 * inactive.mean():.1f}%)"
        )
        print(
            f"  Active pose fields populated: {int(active.sum())} "
            f"({100.0 * active.mean():.1f}%)"
        )
    if inactive.any() and "decision" in df.columns:
        sub = df.loc[inactive, "decision"].astype(str).value_counts()
        print("  Inactive rows by decision:")
        for d, c in sub.items():
            print(f"    {d}: {c}")
    if "orientation_active" in df.columns:
        print(
            "Analysis histograms below use only rows with orientation_active=1.\n"
        )
    else:
        print("Analysis histograms below use only active rows (orient_ratio > 0).\n")


DECISION_COLORS = {
    "MATCHED": "#2ca02c",
    "OFFLOAD_TO_CLOUD": "#1f77b4",
    "BELOW_THRESHOLD": "#bcbd22",
    "BUFFERING": "#7f7f7f",
    "REJECTED_LIVENESS": "#d62728",
    "OUT_OF_RANGE": "#9467bd",
    "ANALYZING": "#aec7e8",
    "UNCERTAIN": "#ffbb78",
    "NO_MATCH": "#c7c7c7",
    "NONE": "#dddddd",
}


# ---------------------------------------------------------------------
# Loading + preprocessing
# ---------------------------------------------------------------------
def load_diag(path: str, label: Optional[str] = None) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Diagnostic log not found: {path}")

    df = pd.read_csv(path)

    # Older logs may not yet have the orientation columns. Bail clearly
    # rather than producing a misleading report.
    required = {"orient_ratio", "mode_raw", "mode", "sim", "distance", "decision"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Diagnostic log missing columns {sorted(missing)}. "
            f"Re-run edge.main with the instrumented schema first; the "
            f"old log was auto-rotated to diagnostic_log.archived_*.csv."
        )

    if label is not None:
        if "experiment_label" not in df.columns:
            raise ValueError("--label given but log has no experiment_label column")
        df = df[df["experiment_label"].astype(str) == label].copy()
        if df.empty:
            raise ValueError(f"No rows with experiment_label='{label}'")

    _print_orientation_gating_report(df)

    # Filter to rows where pose ran (explicit flag preferred).
    if "orientation_active" in df.columns:
        df = df[df["orientation_active"].astype(int) == 1].copy()
    else:
        df = df[df["orient_ratio"] > 0].copy()

    return df


# ---------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------
def _save(fig: plt.Figure, out_dir: str, name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_ratio_histogram(df: pd.DataFrame, out_dir: str) -> str:
    """Threshold validation: does the empirical distribution of
    orient_ratio actually have natural valleys at OVERHEAD_TH / TILTED_TH?
    A well-calibrated classifier should split modes at low-density
    points in the histogram."""
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(df["orient_ratio"], bins=60, color="#888", alpha=0.7, edgecolor="black")
    ax.axvline(OVERHEAD_TH, color="red", linestyle="--",
               label=f"OVERHEAD_TH = {OVERHEAD_TH:.2f}")
    ax.axvline(TILTED_TH, color="blue", linestyle="--",
               label=f"TILTED_TH = {TILTED_TH:.2f}")
    ax.set_xlabel("orient_ratio  =  vertical_dist / eye_dist")
    ax.set_ylabel("frame count")
    ax.set_title("Orientation Ratio Distribution\n(threshold validation)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "01_ratio_histogram.png")


def plot_ratio_per_mode(df: pd.DataFrame, out_dir: str) -> str:
    fig, ax = plt.subplots(figsize=(9, 5))
    for mode, color in MODE_COLORS.items():
        sub = df[df["mode_raw"] == mode]["orient_ratio"]
        if sub.empty:
            continue
        ax.hist(sub, bins=40, alpha=0.55, color=color,
                label=f"{mode}  (n={len(sub)})", edgecolor="black", linewidth=0.3)
    ax.axvline(OVERHEAD_TH, color="red", linestyle="--", linewidth=1)
    ax.axvline(TILTED_TH, color="blue", linestyle="--", linewidth=1)
    ax.set_xlabel("orient_ratio")
    ax.set_ylabel("frame count")
    ax.set_title("Ratio Distribution per Raw Mode\n(should be cleanly separated by thresholds)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "02_ratio_per_mode.png")


def plot_sim_vs_ratio(df: pd.DataFrame, out_dir: str) -> str:
    """Recognition-quality vs orientation: how does cosine similarity
    degrade as the face tilts further from frontal?"""
    rec = df[df["sim"] > 0].copy()
    if rec.empty:
        return ""

    fig, ax = plt.subplots(figsize=(9, 5))
    for decision in rec["decision"].unique():
        sub = rec[rec["decision"] == decision]
        ax.scatter(sub["orient_ratio"], sub["sim"],
                   s=12, alpha=0.45,
                   color=DECISION_COLORS.get(decision, "#000"),
                   label=f"{decision}  (n={len(sub)})")
    ax.axvline(OVERHEAD_TH, color="red", linestyle="--", linewidth=1, alpha=0.7)
    ax.axvline(TILTED_TH, color="blue", linestyle="--", linewidth=1, alpha=0.7)
    ax.axhline(0.80, color="green", linestyle=":", alpha=0.6, label="MATCH_HIGH (0.80)")
    ax.axhline(0.65, color="orange", linestyle=":", alpha=0.6, label="MATCH_MID (0.65)")
    ax.set_xlabel("orient_ratio")
    ax.set_ylabel("cosine similarity")
    ax.set_title("Similarity vs Orientation Ratio\n(per decision class)")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "03_sim_vs_ratio.png")


def plot_mean_sim_binned(df: pd.DataFrame, out_dir: str) -> str:
    """Mean similarity in fixed-width ratio bins. Smooths the scatter to
    show the angle-vs-recognition trend in a paper-friendly form."""
    rec = df[df["sim"] > 0].copy()
    if rec.empty:
        return ""

    bins = np.linspace(0.2, 1.2, 21)  # 0.05 wide
    rec["ratio_bin"] = pd.cut(rec["orient_ratio"], bins, include_lowest=True)
    grouped = rec.groupby("ratio_bin", observed=True)["sim"].agg(["mean", "std", "count"])
    grouped = grouped[grouped["count"] >= 3]

    if grouped.empty:
        return ""

    centers = [(b.left + b.right) / 2 for b in grouped.index]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.errorbar(centers, grouped["mean"], yerr=grouped["std"],
                marker="o", capsize=3, color="#1f77b4")
    ax.axvline(OVERHEAD_TH, color="red", linestyle="--", linewidth=1)
    ax.axvline(TILTED_TH, color="blue", linestyle="--", linewidth=1)
    ax.axhline(0.80, color="green", linestyle=":", alpha=0.6)
    ax.axhline(0.65, color="orange", linestyle=":", alpha=0.6)
    ax.set_xlabel("orient_ratio (binned)")
    ax.set_ylabel("mean cosine similarity ± 1 std")
    ax.set_title("Recognition Quality vs Orientation\n(angle–similarity calibration curve)")
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "04_mean_sim_vs_ratio.png")


def plot_decision_rate_per_mode(df: pd.DataFrame, out_dir: str) -> str:
    """Per-mode breakdown of pipeline decisions. Grounds the question
    'does overhead actually accept fewer faces?' in numbers."""
    if df.empty:
        return ""
    pivot = (
        df.groupby(["mode_raw", "decision"]).size().unstack(fill_value=0)
    )
    pivot = pivot.div(pivot.sum(axis=1), axis=0)  # row-normalised

    fig, ax = plt.subplots(figsize=(10, 5))
    pivot.plot(kind="bar", stacked=True, ax=ax,
               color=[DECISION_COLORS.get(c, "#888") for c in pivot.columns])
    ax.set_ylabel("share of frames")
    ax.set_title("Decision Distribution per Pose Mode\n(overhead deployment evaluation)")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8)
    ax.set_xticklabels(pivot.index, rotation=0)
    return _save(fig, out_dir, "05_decision_rate_per_mode.png")


def plot_sim_vs_distance(df: pd.DataFrame, out_dir: str) -> str:
    rec = df[(df["sim"] > 0) & (df["distance"] > 0)].copy()
    if rec.empty:
        return ""
    fig, ax = plt.subplots(figsize=(9, 5))
    for mode, color in MODE_COLORS.items():
        sub = rec[rec["mode_raw"] == mode]
        if sub.empty:
            continue
        ax.scatter(sub["distance"], sub["sim"],
                   s=12, alpha=0.5, color=color,
                   label=f"{mode}  (n={len(sub)})")
    ax.axhline(0.80, color="green", linestyle=":", alpha=0.6, label="MATCH_HIGH (0.80)")
    ax.axhline(0.65, color="orange", linestyle=":", alpha=0.6, label="MATCH_MID (0.65)")
    ax.set_xlabel("estimated distance (m)")
    ax.set_ylabel("cosine similarity")
    ax.set_title("Similarity vs Distance, by Pose Mode\n(angle × distance interaction)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "06_sim_vs_distance.png")


def plot_mode_stability(df: pd.DataFrame, out_dir: str) -> str:
    """Per-track temporal stability: how often does the *raw* (per-frame)
    mode flip between consecutive frames? Sustained flipping reveals
    threshold values that sit too close to typical ratios."""
    if df.empty or "track_id" not in df.columns:
        return ""

    df_sorted = df.sort_values(["track_id", "timestamp"]).copy()
    df_sorted["mode_changed"] = (
        df_sorted.groupby("track_id")["mode_raw"].transform(
            lambda x: (x != x.shift()).astype(int)
        )
    )
    # ignore the first frame of each track (always counts as a change)
    df_sorted["first_frame"] = df_sorted.groupby("track_id").cumcount() == 0
    df_sorted = df_sorted[~df_sorted["first_frame"]]

    flip_rate_per_track = (
        df_sorted.groupby("track_id")["mode_changed"].mean().reset_index()
    )
    if flip_rate_per_track.empty:
        return ""

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(flip_rate_per_track["mode_changed"], bins=20,
            color="#888", edgecolor="black", alpha=0.8)
    ax.set_xlabel("per-track mode-flip rate  (mode_raw changes / frame)")
    ax.set_ylabel("track count")
    ax.set_title("Raw Mode Stability per Track\n(0.0 = perfectly stable)")
    ax.grid(True, alpha=0.3)
    return _save(fig, out_dir, "07_mode_stability.png")


# ---------------------------------------------------------------------
# Summary tables
# ---------------------------------------------------------------------
def write_summary_csvs(df: pd.DataFrame, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    # Per-mode descriptive statistics
    by_mode = df.groupby("mode_raw").agg(
        n=("orient_ratio", "size"),
        ratio_mean=("orient_ratio", "mean"),
        ratio_std=("orient_ratio", "std"),
        ratio_p10=("orient_ratio", lambda x: np.percentile(x, 10)),
        ratio_p50=("orient_ratio", "median"),
        ratio_p90=("orient_ratio", lambda x: np.percentile(x, 90)),
        sim_mean=("sim", lambda x: x[x > 0].mean() if (x > 0).any() else np.nan),
        sim_p50=("sim", lambda x: np.percentile(x[x > 0], 50) if (x > 0).any() else np.nan),
        face_w_mean=("face_w", "mean"),
        face_h_mean=("face_h", "mean"),
        distance_mean=("distance", "mean"),
    )
    by_mode.to_csv(os.path.join(out_dir, "summary_per_mode.csv"))

    # Per-decision counts cross-tab
    decisions = (
        df.groupby(["mode_raw", "decision"]).size().unstack(fill_value=0)
    )
    decisions.to_csv(os.path.join(out_dir, "summary_decisions_per_mode.csv"))

    # Smoothing impact: how often did smoothing change the classification
    if {"mode_raw", "mode"}.issubset(df.columns):
        smoothing_change = (df["mode_raw"] != df["mode"]).mean()
        with open(os.path.join(out_dir, "summary_smoothing_impact.txt"), "w") as f:
            f.write(f"raw_vs_smoothed_disagreement_rate = {smoothing_change:.4f}\n")
            f.write(
                "Interpretation: fraction of frames where the smoothed "
                "(temporal-majority) mode differs from this-frame raw "
                "classification. Higher means the smoothing window is "
                "doing real work; if 0, smoothing is redundant.\n"
            )


def suggest_calibrated_thresholds(df: pd.DataFrame, out_dir: str) -> dict:
    """
    Suggest calibrated thresholds by looking at where the empirical
    ratio distribution per mode actually sits.

    Strategy:
        - OVERHEAD_TH ≈ midpoint between the 90th percentile of OVERHEAD
          and the 10th percentile of TILTED (lowest-density boundary).
        - TILTED_TH   ≈ midpoint between the 90th percentile of TILTED
          and the 10th percentile of FRONTAL.

    If a mode has too few samples (< 30) we fall back to the current
    configured value rather than producing an unstable estimate.

    The result is *advisory*. The user should eyeball plot 02 (ratio
    per mode) and confirm the suggestions sit in genuine valleys before
    promoting them into config/settings.py.
    """
    suggestions = {"overhead_th": OVERHEAD_TH, "tilted_th": TILTED_TH}
    notes = []

    overhead = df[df["mode_raw"] == "OVERHEAD"]["orient_ratio"]
    tilted   = df[df["mode_raw"] == "TILTED"]["orient_ratio"]
    frontal  = df[df["mode_raw"] == "FRONTAL"]["orient_ratio"]

    if len(overhead) >= 30 and len(tilted) >= 30:
        cand = (np.percentile(overhead, 90) + np.percentile(tilted, 10)) / 2
        suggestions["overhead_th"] = float(cand)
        notes.append(
            f"OVERHEAD_TH suggested = {cand:.3f}  "
            f"(p90(OVERHEAD)={np.percentile(overhead,90):.3f}, "
            f"p10(TILTED)={np.percentile(tilted,10):.3f})"
        )
    else:
        notes.append(
            f"OVERHEAD_TH unchanged ({OVERHEAD_TH:.3f}); "
            f"insufficient samples (overhead n={len(overhead)}, tilted n={len(tilted)})"
        )

    if len(tilted) >= 30 and len(frontal) >= 30:
        cand = (np.percentile(tilted, 90) + np.percentile(frontal, 10)) / 2
        suggestions["tilted_th"] = float(cand)
        notes.append(
            f"TILTED_TH suggested = {cand:.3f}  "
            f"(p90(TILTED)={np.percentile(tilted,90):.3f}, "
            f"p10(FRONTAL)={np.percentile(frontal,10):.3f})"
        )
    else:
        notes.append(
            f"TILTED_TH unchanged ({TILTED_TH:.3f}); "
            f"insufficient samples (tilted n={len(tilted)}, frontal n={len(frontal)})"
        )

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "calibration_suggestions.txt"), "w") as f:
        f.write("ORIENTATION THRESHOLD CALIBRATION\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Currently configured:\n")
        f.write(f"  ORIENTATION_OVERHEAD_TH = {OVERHEAD_TH:.3f}\n")
        f.write(f"  ORIENTATION_TILTED_TH   = {TILTED_TH:.3f}\n\n")
        f.write("Suggested (from empirical distribution):\n")
        f.write(f"  ORIENTATION_OVERHEAD_TH = {suggestions['overhead_th']:.3f}\n")
        f.write(f"  ORIENTATION_TILTED_TH   = {suggestions['tilted_th']:.3f}\n\n")
        f.write("Notes:\n")
        for n in notes:
            f.write(f"  - {n}\n")
        f.write(
            "\nApply by editing config/settings.py. Re-run a calibration "
            "session afterwards and verify that plot 02_ratio_per_mode.png "
            "shows the new cuts sitting in low-density valleys between modes.\n"
        )
    return suggestions


# ---------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------
def print_summary(df: pd.DataFrame, suggestions: dict) -> None:
    print("\n" + "=" * 64)
    print("ORIENTATION SUBSYSTEM -- VALIDATION REPORT")
    print("=" * 64)

    filt_desc = (
        "orientation_active=1"
        if "orientation_active" in df.columns
        else "orient_ratio > 0"
    )
    print(f"\nTotal frames analysed ({filt_desc}): {len(df)}")
    print(f"Distinct tracks: {df['track_id'].nunique() if 'track_id' in df.columns else 'NA'}")
    if "experiment_label" in df.columns:
        labels = sorted(df["experiment_label"].astype(str).unique())
        labels_str = ", ".join(repr(l) for l in labels[:8])
        if len(labels) > 8:
            labels_str += f", ... (+{len(labels) - 8} more)"
        print(f"Experiment labels present: [{labels_str}]")

    print("\n[ Per raw-mode statistics ]")
    by_mode = df.groupby("mode_raw")["orient_ratio"].agg(["count", "mean", "std", "median"])
    print(by_mode.round(3).to_string())

    print("\n[ Smoothing impact ]")
    if {"mode_raw", "mode"}.issubset(df.columns):
        rate = (df["mode_raw"] != df["mode"]).mean()
        print(f"  raw_vs_smoothed disagreement rate: {rate:.4f}")

    print("\n[ Calibration suggestions ]")
    print(f"  current OVERHEAD_TH = {OVERHEAD_TH:.3f}  ->  suggested {suggestions['overhead_th']:.3f}")
    print(f"  current TILTED_TH   = {TILTED_TH:.3f}  ->  suggested {suggestions['tilted_th']:.3f}")
    print()


# ---------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--diag", default="data/diagnostic_log.csv",
                        help="path to diagnostic_log.csv")
    parser.add_argument("--out", default="data/plots/orientation",
                        help="output directory for plots + summaries")
    parser.add_argument("--label", default=None,
                        help="restrict analysis to a single experiment_label")
    parser.add_argument("--per-label", action="store_true",
                        help="also emit per-experiment_label sub-reports")
    args = parser.parse_args()

    df = load_diag(args.diag, label=args.label)

    out_dir = args.out
    if args.label:
        out_dir = os.path.join(out_dir, f"label_{args.label}")

    plots = []
    plots.append(plot_ratio_histogram(df, out_dir))
    plots.append(plot_ratio_per_mode(df, out_dir))
    plots.append(plot_sim_vs_ratio(df, out_dir))
    plots.append(plot_mean_sim_binned(df, out_dir))
    plots.append(plot_decision_rate_per_mode(df, out_dir))
    plots.append(plot_sim_vs_distance(df, out_dir))
    plots.append(plot_mode_stability(df, out_dir))
    write_summary_csvs(df, out_dir)
    suggestions = suggest_calibrated_thresholds(df, out_dir)

    print_summary(df, suggestions)
    print(f"Outputs written to: {os.path.abspath(out_dir)}")
    for p in plots:
        if p:
            print(f"  - {os.path.basename(p)}")

    # Per-label sub-reports for multi-condition captures
    if args.per_label and "experiment_label" in df.columns and args.label is None:
        labels = [l for l in df["experiment_label"].astype(str).unique() if l]
        for label in labels:
            sub = df[df["experiment_label"].astype(str) == label]
            if len(sub) < 30:
                continue
            sub_dir = os.path.join(args.out, f"label_{label}")
            for fn in (plot_ratio_histogram, plot_ratio_per_mode,
                       plot_sim_vs_ratio, plot_mean_sim_binned,
                       plot_decision_rate_per_mode, plot_sim_vs_distance,
                       plot_mode_stability):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    fn(sub, sub_dir)
            write_summary_csvs(sub, sub_dir)
            suggest_calibrated_thresholds(sub, sub_dir)
            print(f"  -> per-label report: {sub_dir}")


if __name__ == "__main__":
    main()
