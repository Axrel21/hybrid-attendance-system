#!/usr/bin/env python3
"""
Optional pre-flight check: verify OpenCV HighGUI is available.

Usage (inside the project venv on the Pi or dev machine):

    python deployment/pi/validate_opencv_gui.py

Exit code 0 if GUI backend is present; non-zero if headless wheel detected.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python deployment/pi/validate_opencv_gui.py` from repo root
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from edge.opencv_highgui import check_highgui_from_build_info  # noqa: E402


def main() -> int:
    ok, msg = check_highgui_from_build_info()
    print(msg)
    if not ok:
        print(
            "\nFor Raspberry Pi with HDMI / desktop / VNC, see:\n"
            "  deployment/pi/OPENCV_GUI_RASPBERRY_PI.md",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
