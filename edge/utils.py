from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import numpy as np


Point = Tuple[int, int]
Box = Tuple[int, int, int, int]  # (x, y, w, h)


def is_valid_face(
    crop_bgr: np.ndarray,
    landmarks: Sequence[Point],
    box: Box,
    frame_w: int,
    frame_h: int,
    *,
    min_size_px: int = 36,
    min_eye_distance_px: float = 6.0,
) -> bool:
    """
    Lightweight validation gate for detector outputs.

    Intended to reject:
    - empty / out-of-frame crops
    - tiny boxes (common false positives at low resolution)
    - malformed landmarks (missing, degenerate, out-of-box)
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return False

    if len(crop_bgr.shape) < 2:
        return False

    x, y, w, h = box
    if w <= 0 or h <= 0:
        return False

    # Bounds check (allow slight overlap; reject clearly invalid)
    if x >= frame_w or y >= frame_h:
        return False
    if x + w <= 0 or y + h <= 0:
        return False

    if w < min_size_px or h < min_size_px:
        return False

    # Aspect ratio sanity: very thin/tall boxes are typically junk.
    ar = w / float(h + 1e-6)
    if ar < 0.35 or ar > 2.5:
        return False

    if landmarks is None or len(landmarks) != 5:
        return False

    # Landmarks should be finite ints and fall inside the box (with small tolerance).
    tol = 3
    pts = []
    for (lx, ly) in landmarks:
        if lx is None or ly is None:
            return False
        if not (np.isfinite(lx) and np.isfinite(ly)):
            return False
        pts.append((int(lx), int(ly)))

    for (lx, ly) in pts:
        if lx < x - tol or lx > x + w + tol or ly < y - tol or ly > y + h + tol:
            return False

    # Degenerate landmarks (all same point) => bad detection.
    if len(set(pts)) < 3:
        return False

    # Eye distance sanity (assumes YuNet order: left_eye, right_eye, nose, left_mouth, right_mouth).
    (ex1, ey1), (ex2, ey2) = pts[0], pts[1]
    eye_dist = float(np.hypot(ex2 - ex1, ey2 - ey1))
    if eye_dist < min_eye_distance_px:
        return False

    return True
