"""Local surveillance runtime — webcam feed with live occupancy overlay."""

from __future__ import annotations

import cv2

from surveillance.camera import WebcamCapture
from surveillance.occupancy import estimate_occupancy

_WINDOW_TITLE = "Surveillance (Track 1)"
_QUIT_KEYS = {ord("q"), ord("Q"), 27}


def main() -> int:
    camera = WebcamCapture(device_index=0)

    try:
        for frame in camera.frames():
            count = estimate_occupancy(frame)

            cv2.putText(
                frame,
                f"Occupancy: {count}",
                (12, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow(_WINDOW_TITLE, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in _QUIT_KEYS:
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
