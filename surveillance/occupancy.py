"""Fixed occupancy estimator — OpenCV HOG person detector only."""

from __future__ import annotations

import cv2
import numpy as np

_HOG = cv2.HOGDescriptor()
_HOG.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

_MIN_DETECTION_WEIGHT = 0.5


def estimate_occupancy(frame: np.ndarray) -> int:
    """Return a non-negative integer occupancy estimate for the latest frame."""
    if frame is None or frame.size == 0:
        return 0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, weights = _HOG.detectMultiScale(
        gray,
        winStride=(8, 8),
        padding=(4, 4),
        scale=1.05,
    )

    count = sum(1 for weight in weights if weight >= _MIN_DETECTION_WEIGHT)
    return max(0, int(count))
