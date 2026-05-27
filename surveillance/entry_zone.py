"""Hardcoded doorway ROI for experimental entry-zone correlation."""

from __future__ import annotations

# Normalized rectangle (x1, y1, x2, y2) in 0..1 frame coordinates — left edge / doorway band.
ENTRY_ZONE_RECT = (0.0, 0.2, 0.25, 0.8)


def centroid_in_entry_zone(cx: float, cy: float) -> bool:
    x1, y1, x2, y2 = ENTRY_ZONE_RECT
    return x1 <= cx <= x2 and y1 <= cy <= y2


def centroid_in_entry_zone_pixels(
    cx: float,
    cy: float,
    *,
    frame_width: int,
    frame_height: int,
) -> bool:
    if frame_width <= 0 or frame_height <= 0:
        return False
    nx = cx / frame_width
    ny = cy / frame_height
    return centroid_in_entry_zone(nx, ny)
