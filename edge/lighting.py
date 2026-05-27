import cv2
import numpy as np


def assess_lighting(frame):
    """Frame-level illumination assessment (no enhancement)."""
    if frame.ndim == 2:
        gray = frame
    else:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    mean_brightness = float(np.mean(gray))
    contrast = float(np.std(gray))
    dark_ratio = float(np.mean(gray < 50))

    if mean_brightness < 55:
        level = "LOW"
    elif mean_brightness < 95:
        level = "DIM"
    else:
        level = "NORMAL"

    return {
        "level": level,
        "mean": mean_brightness,
        "contrast": contrast,
        "dark_ratio": dark_ratio,
    }
