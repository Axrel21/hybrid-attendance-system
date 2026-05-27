# edge/telemetry.py
"""
Research telemetry: frame-level timing, interval stability, CSV logging,
optional corner overlay, and event-triggered debug JPEG capture.

Designed to stay lightweight: one CSV row per frame (optional subsampling),
buffered disk writes via the caller's file handle, and infrequent imwrite.
"""
from __future__ import annotations

import csv
import os
import time
from collections import deque
from typing import Deque, List, Optional, Sequence

import cv2

from config.logging_setup import LOG_RUNTIME

TELEMETRY_CSV_COLUMNS: List[str] = [
    "timestamp",
    "frame_idx",
    "experiment_label",
    "fps_rolling",
    "dt_ms",
    "dt_std_ms",
    "t_capture_ms",
    "t_detect_ms",
    "t_tracks_ms",
    "t_liveness_max_ms",
    "t_embed_max_ms",
    "t_match_max_ms",
    "t_overlay_ms",
    "t_post_ms",
    "t_total_ms",
    "cpu_pct",
    "mem_mb",
    "cpu_temp_c",
    "fan_state",
    "num_tracks",
    "num_faces_valid",
    "yunet_raw",
    "yunet_kept",
    "max_live_conf",
    "max_sim",
]


def rotate_if_schema_changed(path: str, expected: Sequence[str]) -> bool:
    """Return True if caller should write CSV header (new or rotated file)."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return True
    try:
        with open(path, "r", newline="") as f:
            header = next(csv.reader(f), [])
    except Exception:
        ts = time.strftime("%Y%m%d_%H%M%S")
        os.rename(path, path.replace(".csv", f".unreadable_{ts}.csv"))
        return True
    if list(header) == list(expected):
        return False
    ts = time.strftime("%Y%m%d_%H%M%S")
    archived = path.replace(".csv", f".archived_{ts}.csv")
    os.rename(path, archived)
    LOG_RUNTIME.info(
        "Telemetry CSV schema changed; archived %s",
        os.path.basename(archived),
    )
    return True


def interval_mean_std(samples: Deque[float]) -> tuple[float, float]:
    """Mean and population std of frame intervals (ms); (0,0) if len < 2."""
    if len(samples) < 2:
        return 0.0, 0.0
    data = list(samples)
    mean = sum(data) / len(data)
    var = sum((x - mean) ** 2 for x in data) / len(data)
    return mean, var**0.5


def draw_telemetry_lines(
    frame,
    lines: List[str],
    origin_x: int = 8,
    origin_y: int = 22,
    fg: tuple[int, int, int] = (0, 255, 128),
) -> None:
    """Small monospace-style stack; outline for readability on busy frames."""
    y = origin_y
    for line in lines:
        cv2.putText(
            frame,
            line,
            (origin_x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            line,
            (origin_x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            fg,
            1,
            cv2.LINE_AA,
        )
        y += 16


class TelemetryFrameState:
    """Rolling frame-interval stats for one run."""

    def __init__(self, dt_window: int) -> None:
        self.prev_loop_start_pc: Optional[float] = None
        self.dt_samples: Deque[float] = deque(maxlen=max(2, dt_window))
        self.frame_idx = 0

    def tick_dt(self, loop_start_pc: float) -> tuple[int, float, float, float]:
        """
        Update interval deque using elapsed since previous frame's loop start.
        Returns (frame_idx, dt_ms, mean_dt_ms, std_dt_ms).
        """
        self.frame_idx += 1
        dt_ms = 0.0
        if self.prev_loop_start_pc is not None:
            dt_ms = (loop_start_pc - self.prev_loop_start_pc) * 1000.0
            self.dt_samples.append(dt_ms)
        self.prev_loop_start_pc = loop_start_pc
        mean_dt, std_dt = interval_mean_std(self.dt_samples)
        return self.frame_idx, dt_ms, mean_dt, std_dt


class DebugFrameWriter:
    """
    Event-triggered JPEG saves under the experiment session debug_frames/
    directory (or DEBUG_FRAMES_DIR when set as an escape hatch).

    Rate-limited and capped per process.
    """

    def __init__(
        self,
        root_dir: str,
        min_interval_s: float,
        max_per_run: int,
        jpeg_quality: int,
    ) -> None:
        self._root = root_dir
        self._min_interval_s = min_interval_s
        self._max_per_run = max_per_run
        self._jpeg_quality = max(1, min(100, int(jpeg_quality)))
        self._count = 0
        self._last_mono = 0.0
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for sub in (
            "manual",
            "spoof_cases",
            "liveness_failures",
            "borderline_recognition",
            "low_detection_conf",
            "sampled",
            "misc",
        ):
            os.makedirs(os.path.join(self._root, sub), exist_ok=True)

    def _allowed(self) -> bool:
        if self._count >= self._max_per_run:
            return False
        if (time.monotonic() - self._last_mono) < self._min_interval_s:
            return False
        return True

    def save(
        self,
        frame,
        subdir: str,
        tag: str,
        extra: str = "",
    ) -> None:
        if frame is None or frame.size == 0 or not self._allowed():
            return
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:80]
        es = "".join(c if c.isalnum() or c in "-_." else "_" for c in extra)[:40]
        suffix = f"_{es}" if es else ""
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            self._root,
            subdir,
            f"{ts}_{safe}{suffix}.jpg",
        )
        try:
            img = frame.copy()
            cv2.imwrite(
                path,
                img,
                [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
            )
            self._count += 1
            self._last_mono = time.monotonic()
        except Exception:
            pass


def classify_debug_events(
    track_id: int,
    decision: str,
    lbl: str,
    yunet_score: Optional[float],
    yunet_th: float,
) -> list[tuple[str, str, str]]:
    """
    Return list of (subdir, reason_tag, extra) for this track's dbg snapshot.
    Empty if no rule matched.
    """
    out: list[tuple[str, str, str]] = []
    tid = str(track_id)

    if lbl == "SPOOF":
        out.append(("spoof_cases", "SPOOF", tid))
    if decision == "REJECTED_LIVENESS" or lbl == "SPOOF":
        out.append(("liveness_failures", decision, tid))

    if decision == "BELOW_THRESHOLD":
        out.append(("borderline_recognition", "below_th", tid))
    elif decision == "OFFLOAD_TO_CLOUD":
        out.append(("borderline_recognition", "offload_mid", tid))

    if (
        yunet_th > 0.0
        and yunet_score is not None
        and yunet_score < yunet_th
        and decision != "NO_MATCH"
    ):
        out.append(("low_detection_conf", "yunet_low", f"{tid}_s{yunet_score:.2f}"))

    return out
