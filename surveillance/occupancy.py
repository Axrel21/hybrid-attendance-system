"""Occupancy estimator — YOLOv8n person detector, CPU only (D3 Track 2)."""

from __future__ import annotations

import os
from typing import Any

import numpy as np

_MODEL: Any | None = None
_PERSON_CLASS_ID = 0
_DEFAULT_CONFIDENCE = 0.35


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


def estimate_occupancy(frame: np.ndarray) -> int:
    """Return a non-negative integer person count for the latest frame."""
    if frame is None or frame.size == 0:
        return 0

    results = _get_model().predict(
        frame,
        conf=_confidence_threshold(),
        classes=[_PERSON_CLASS_ID],
        device="cpu",
        imgsz=320,
        verbose=False,
    )
    if not results:
        return 0

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return 0

    return max(0, int(len(boxes)))
