"""
Lightweight helpers for OpenCV HighGUI (cv2.namedWindow / imshow / waitKey).

Used to fail fast with a clear message when HEADLESS=0 but the active
environment only has opencv-python-headless (no GTK/Qt backend).
"""
from __future__ import annotations

import os
import re

import cv2


def check_highgui_from_build_info() -> tuple[bool, str]:
    """
    Inspect cv2.getBuildInformation() for a usable GUI backend.

    Returns
    -------
    (True, summary_line) if HighGUI is likely available.
    (False, reason) if the wheel is almost certainly headless-only.
    """
    try:
        info = cv2.getBuildInformation()
    except Exception as exc:  # pragma: no cover
        return False, f"getBuildInformation() failed: {exc}"

    # Typical headless wheel: "GUI:                           NONE"
    if re.search(r"^\s*GUI:\s+NONE\s*$", info, re.MULTILINE):
        return (
            False,
            "OpenCV reports GUI: NONE (use opencv-python with GTK, not "
            "opencv-python-headless). See deployment/OPENCV_GUI_RASPBERRY_PI.md",
        )

    # Positive signals (any one is enough for Linux Pi / desktop)
    for pattern, label in (
        (r"^\s*GTK\+:\s+YES", "GTK+"),
        (r"^\s*QT:\s+YES", "Qt"),
        (r"^\s*Cocoa:\s+YES", "Cocoa"),
    ):
        if re.search(pattern, info, re.MULTILINE):
            return True, f"HighGUI backend: {label}"

    # Non-NONE GUI line but no YES — unusual; allow and let namedWindow throw.
    if "GUI:" in info:
        return True, "GUI reported (unknown backend; namedWindow will validate)"

    return True, "Could not classify GUI line; assuming HighGUI OK"


def skip_gui_precheck() -> bool:
    """True if user set SKIP_OPENCV_GUI_CHECK=1 to bypass build-info check."""
    return os.environ.get("SKIP_OPENCV_GUI_CHECK", "0") in (
        "1", "true", "True", "yes",
    )
