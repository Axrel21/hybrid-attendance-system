"""Occupancy — YOLOv8n + ByteTrack, anonymous local track IDs (D3 Track 3)."""

from __future__ import annotations

import os
from typing import Any

import cv2
import numpy as np

_MODEL: Any | None = None
_PERSON_CLASS_ID = 0
_DEFAULT_CONFIDENCE = 0.35
_TRACKER_CONFIG = "bytetrack.yaml"

_active_track_ids: list[int] = []


def _confidence_threshold() -> float:
    raw = os.environ.get("SURVEILLANCE_CONFIDENCE", str(_DEFAULT_CONFIDENCE))
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_CONFIDENCE


def _get_model() -> Any:
    global _MODEL
    if _MODEL is None:
        from ultralytics import YOLO

        _MODEL = YOLO("yolov8n.pt")
    return _MODEL


def get_active_track_ids() -> list[int]:
    """Sorted active track IDs from the latest frame (runtime-local, not identity)."""
    return list(_active_track_ids)


def _draw_tracking_overlay(frame: np.ndarray, result: Any) -> None:
    global _active_track_ids
    track_ids: list[int] = []

    boxes = result.boxes
    if boxes is None or len(boxes) == 0 or boxes.id is None:
        _active_track_ids = []
        return

    xyxy = boxes.xyxy.cpu().numpy()
    ids = boxes.id.int().cpu().tolist()

    for box, track_id in zip(xyxy, ids):
        tid = int(track_id)
        track_ids.append(tid)
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 128, 0), 2)
        cv2.putText(
            frame,
            f"#{tid}",
            (x1, max(y1 - 6, 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 128, 0),
            1,
            cv2.LINE_AA,
        )

    _active_track_ids = sorted(set(track_ids))

    y = 58
    cv2.putText(
        frame,
        "Track IDs:",
        (12, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 200, 100),
        1,
        cv2.LINE_AA,
    )
    for tid in _active_track_ids[:10]:
        y += 20
        cv2.putText(
            frame,
            f"#{tid}",
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 200, 100),
            1,
            cv2.LINE_AA,
        )


def estimate_occupancy(frame: np.ndarray) -> int:
    """Return count of unique active tracked persons; draws track overlay on frame."""
    global _active_track_ids

    if frame is None or frame.size == 0:
        _active_track_ids = []
        return 0

    results = _get_model().track(
        frame,
        persist=True,
        tracker=_TRACKER_CONFIG,
        conf=_confidence_threshold(),
        classes=[_PERSON_CLASS_ID],
        device="cpu",
        imgsz=320,
        verbose=False,
    )
    if not results:
        _active_track_ids = []
        return 0

    _draw_tracking_overlay(frame, results[0])
    return max(0, len(_active_track_ids))
