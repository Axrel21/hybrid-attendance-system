import cv2
import numpy as np
from collections import deque


# Deployment defaults: metrics on, debug off. Flip for illumination experiments.
ILLUM_DEBUG_ENABLED = False
ILLUM_SMOOTHING_ENABLED = False


def _classify_level(mean_brightness: float) -> str:
    if mean_brightness < 55:
        return "LOW"
    elif mean_brightness < 95:
        return "DIM"
    return "NORMAL"


def assess_lighting(frame, gray=None):
    """Frame-level illumination assessment (no enhancement)."""
    if gray is None:
        if frame.ndim == 2:
            gray = frame
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = gray

    mean_brightness = float(np.mean(gray))
    contrast = float(np.std(gray))
    dark_ratio = float(np.mean(gray < 50))

    return {
        "level": _classify_level(mean_brightness),
        "mean": mean_brightness,
        "contrast": contrast,
        "dark_ratio": dark_ratio,
    }


class LightingSmoother:
    """Rolling-window smoother over frame-level illumination metrics.

    Available for future experiments; not used in default deployment runtime.
    """

    def __init__(self, history_len: int = 5):
        self._history = deque(maxlen=max(1, int(history_len)))

    def update(self, frame, gray=None) -> dict:
        self._history.append(assess_lighting(frame, gray=gray))
        n = len(self._history)
        mean = sum(r["mean"] for r in self._history) / n
        contrast = sum(r["contrast"] for r in self._history) / n
        dark_ratio = sum(r["dark_ratio"] for r in self._history) / n
        return {
            "level": _classify_level(mean),
            "mean": mean,
            "contrast": contrast,
            "dark_ratio": dark_ratio,
        }
