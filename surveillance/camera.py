"""Laptop webcam capture for the local surveillance prototype."""

from __future__ import annotations

from typing import Iterator

import cv2
import numpy as np


class WebcamCapture:
    """Open the default laptop webcam and yield BGR frames."""

    def __init__(self, device_index: int = 0, width: int = 320, height: int = 240) -> None:
        self._device_index = device_index
        self._cap = cv2.VideoCapture(device_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open webcam at index {device_index}")

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def read(self) -> tuple[bool, np.ndarray | None]:
        return self._cap.read()

    def frames(self) -> Iterator[np.ndarray]:
        """Yield frames until capture fails or the caller stops iterating."""
        try:
            while True:
                ok, frame = self.read()
                if not ok or frame is None:
                    break
                yield frame
        finally:
            self.release()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> WebcamCapture:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
