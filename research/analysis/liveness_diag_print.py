"""Print liveness PAD statistics from a diagnostic CSV (legacy script)."""
from __future__ import annotations

import argparse
from typing import Optional

import pandas as pd


def run(diag_path: str) -> None:
    df = pd.read_csv(diag_path)

    real = df[df.lbl == "REAL"]
    spoof = df[df.lbl == "SPOOF"]
    unc = df[df.lbl == "UNCERTAIN"]

    print("\n" + "=" * 60)
    print("LIVENESS DIAGNOSTIC ANALYSIS")
    print("=" * 60)

    print("\n[ OVERALL LABEL DISTRIBUTION ]")
    print(df["lbl"].value_counts())

    print("\n[ OVERALL DECISION DISTRIBUTION ]")
    print(df["decision"].value_counts())

    print("\n" + "=" * 60)
    print("REAL USER ANALYSIS")
    print("=" * 60)

    print(f"\nTotal REAL frames: {len(real)}")

    print("\nDecision Breakdown:")
    print(real["decision"].value_counts())

    print("\nReason Breakdown:")
    print(real["reason"].value_counts())

    print("\nMotion Statistics:")
    print(
        real[
            [
                "avg_mag",
                "avg_ang_var",
                "avg_mag_var",
                "avg_area_var",
                "rigid_ratio",
            ]
        ].describe()
    )

    print("\n" + "=" * 60)
    print("SPOOF ANALYSIS")
    print("=" * 60)

    print(f"\nTotal SPOOF frames: {len(spoof)}")

    print("\nDecision Breakdown:")
    print(spoof["decision"].value_counts())

    print("\nReason Breakdown:")
    print(spoof["reason"].value_counts())

    print("\nMotion Statistics:")
    print(
        spoof[
            [
                "avg_mag",
                "avg_ang_var",
                "avg_mag_var",
                "avg_area_var",
                "rigid_ratio",
            ]
        ].describe()
    )

    print("\n" + "=" * 60)
    print("UNCERTAIN ANALYSIS")
    print("=" * 60)

    print(f"\nTotal UNCERTAIN frames: {len(unc)}")

    print("\nDecision Breakdown:")
    print(unc["decision"].value_counts())

    print("\nReason Breakdown:")
    print(unc["reason"].value_counts())

    print("\nMotion Statistics:")
    print(
        unc[
            [
                "avg_mag",
                "avg_ang_var",
                "avg_mag_var",
                "avg_area_var",
                "rigid_ratio",
            ]
        ].describe()
    )

    print("\n" + "=" * 60)
    print("END OF ANALYSIS")
    print("=" * 60)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--diag",
        default="data/diagnostic_log.csv",
        help="path to diagnostic_log.csv",
    )
    args = p.parse_args(argv)
    run(args.diag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
