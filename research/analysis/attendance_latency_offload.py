"""Quick latency + offload-rate summary from attendance and diagnostic CSVs."""
from __future__ import annotations

import argparse

import pandas as pd


def run(attendance_path: str, diagnostic_path: str) -> None:
    df = pd.read_csv(attendance_path)

    avg_latency = df["latency"].mean()
    print(f"Average Edge Latency: {avg_latency:.2f} ms")

    total_spoofs_attempted = len(
        df[df["reason"].str.contains("Screen|Photo|Static", na=False)]
    )
    spoofs_rejected = len(df[df["liveness_label"] == "SPOOF"])
    srr = (
        (spoofs_rejected / total_spoofs_attempted) * 100
        if total_spoofs_attempted > 0
        else 0
    )
    print(f"Spoof Rejection Rate: {srr:.2f}%")

    diag_df = pd.read_csv(diagnostic_path)
    offloads = len(diag_df[diag_df["decision"] == "OFFLOAD_TO_CLOUD"])
    offload_rate = (offloads / len(diag_df)) * 100 if len(diag_df) > 0 else 0
    print(f"Server Offload Rate: {offload_rate:.2f}%")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--attendance",
        default="data/attendance_log.csv",
        help="path to attendance_log.csv",
    )
    p.add_argument(
        "--diag",
        default="data/diagnostic_log.csv",
        help="path to diagnostic_log.csv (for offload rate)",
    )
    args = p.parse_args()
    run(args.attendance, args.diag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
