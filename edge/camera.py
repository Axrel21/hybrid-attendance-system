# edge/camera.py
"""
Camera abstraction layer for the attendance edge pipeline.

Provides a unified .read() -> (bool, bgr_frame) interface so the rest of
the pipeline is completely independent of the underlying capture backend.

Backends
--------
OpenCVCamera
    Wraps cv2.VideoCapture.  Works on laptop webcams and USB cameras.
    Selected by CAMERA_BACKEND = "opencv" in config/settings.py.

PiCamera2Camera
    Wraps the Picamera2 library which uses the libcamera stack on
    Raspberry Pi OS Bullseye / Bookworm.  Required for the Pi Camera
    Module 2 because cv2.VideoCapture(0) is unreliable with libcamera.
    Selected by CAMERA_BACKEND = "picamera2".

Usage (in edge/main.py)
-----------------------
    from edge.camera import CameraSource
    cap = CameraSource(settings.CAMERA_BACKEND, width=640, height=480)
    ret, frame = cap.read()   # frame is always BGR uint8
    cap.release()

K_FOCAL recalibration after mounting Pi Camera Module 2
--------------------------------------------------------
Pi Camera Module 2 has a slightly different FOV than a typical laptop
webcam (~67.5 deg diagonal vs ~60 deg), so K_FOCAL must be re-measured:

    1. Stand exactly 2.0 m from the mounted camera.
    2. Run the pipeline (VERBOSE_DEBUG=1 shows face box dimensions).
    3. Record fw (face box width) and fh (face box height) from the overlay.
    4. New K_FOCAL = 2.0 * sqrt(fw * fh)
    5. Set CAMERA_BACKEND = "picamera2" and K_FOCAL = <measured value>
       in config/settings.py.

The formula comes from the distance estimator in main.py:
    distance = K_FOCAL / sqrt(fw * fh)
Inverting at a known distance gives the calibration constant.
"""

from __future__ import annotations

import cv2
import numpy as np


class OpenCVCamera:
    """
    cv2.VideoCapture wrapper.

    Suitable for laptop webcams and USB cameras.  Sets the requested
    resolution immediately after opening so YuNet receives the expected
    640x480 input without the per-frame setInputSize calls.
    """

    def __init__(self, width: int = 640, height: int = 480) -> None:
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            raise RuntimeError(
                "OpenCVCamera: cv2.VideoCapture(0) failed to open. "
                "Ensure a webcam is connected or switch to CAMERA_BACKEND=picamera2."
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # Reduce internal buffer to keep latency low; we read every frame.
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self) -> tuple[bool, np.ndarray]:
        """Return (True, bgr_frame) or (False, empty_array) on failure."""
        return self._cap.read()

    def release(self) -> None:
        self._cap.release()

    @property
    def is_open(self) -> bool:
        return self._cap.isOpened()


class PiCamera2Camera:
    """
    Picamera2 wrapper for Pi Camera Module 2 via the libcamera stack.

    Configures the camera to output BGR888 frames directly so no
    colour-space conversion is needed in the pipeline.  If BGR888 is
    not supported by the installed Picamera2 version the class falls
    back to RGB888 and converts to BGR with a zero-copy numpy view flip.

    The Picamera2 library is imported lazily inside __init__ so that
    importing edge.camera on a non-Pi machine does not fail.  Only a
    runtime call to PiCamera2Camera() will raise ImportError if
    picamera2 is not installed.
    """

    _BGR_FORMAT = "BGR888"
    _RGB_FORMAT = "RGB888"

    def __init__(self, width: int = 640, height: int = 480) -> None:
        try:
            from picamera2 import Picamera2  # lazy import — Pi only
        except ImportError as e:
            raise ImportError(
                "PiCamera2Camera requires the 'picamera2' package. "
                "Install it with: pip install picamera2\n"
                f"Original error: {e}"
            ) from e

        self._cam = Picamera2()

        # Try BGR888 first (native BGR, no conversion cost).
        # Fall back to RGB888 and flip in read() if not supported.
        try:
            config = self._cam.create_preview_configuration(
                main={"size": (width, height), "format": self._BGR_FORMAT}
            )
            self._cam.configure(config)
            self._native_bgr = True
        except Exception:
            config = self._cam.create_preview_configuration(
                main={"size": (width, height), "format": self._RGB_FORMAT}
            )
            self._cam.configure(config)
            self._native_bgr = False

        self._cam.start()

    def read(self) -> tuple[bool, np.ndarray]:
        """
        Capture a single frame from the Pi Camera.

        Returns (True, bgr_frame) where bgr_frame is a (H, W, 3) uint8
        numpy array in BGR channel order, compatible with OpenCV.
        """
        frame = self._cam.capture_array("main")
        if not self._native_bgr:
            # RGB -> BGR: reverse last axis in-place view (no copy).
            frame = frame[:, :, ::-1]
        return True, frame

    def release(self) -> None:
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        return True  # Picamera2 has no isOpened() equivalent


def CameraSource(
    backend: str = "opencv",
    width: int = 640,
    height: int = 480,
) -> OpenCVCamera | PiCamera2Camera:
    """
    Factory function — return the appropriate camera backend.

    Parameters
    ----------
    backend : str
        "opencv"    — OpenCVCamera (laptop / USB webcam)
        "picamera2" — PiCamera2Camera (Pi Camera Module 2)
    width, height : int
        Capture resolution. Both backends configure this at init time
        so the main loop never needs to call setInputSize mid-run.

    Raises
    ------
    ValueError
        If backend is not a recognised string.
    """
    backend = backend.strip().lower()
    if backend == "opencv":
        return OpenCVCamera(width=width, height=height)
    if backend == "picamera2":
        return PiCamera2Camera(width=width, height=height)
    raise ValueError(
        f"Unknown CAMERA_BACKEND '{backend}'. "
        "Valid values: 'opencv', 'picamera2'."
    )
