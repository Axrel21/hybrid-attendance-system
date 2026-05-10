# edge/camera.py
"""
Camera abstraction layer for the attendance edge pipeline.

Provides a unified .read() -> (bool, bgr_ndarray) interface so every
module downstream is completely independent of the capture backend.

Backends
--------
"opencv"
    cv2.VideoCapture(0).  Works on laptop webcams and USB cameras.

"libcamera"
    Auto-selects between libcamera_gstreamer and libcamera_subprocess
    based on whether this OpenCV build has GStreamer support.
    Use this on Raspberry Pi with Conda Python — it avoids the libcamera
    Python ABI mismatch that breaks the picamera2 backend.

"libcamera_gstreamer"
    GStreamer pipeline using the libcamerasrc element.  Requires OpenCV
    compiled with GStreamer support AND gstreamer1.0-plugins-bad installed
    for the libcamerasrc GStreamer plugin.  No Python version dependency —
    the camera is accessed entirely through the GStreamer C library.

"libcamera_subprocess"
    Runs rpicam-vid as a background subprocess, reads MJPEG frames from
    its stdout, decodes them with cv2.imdecode.  Most reliable option on
    any Conda/virtualenv Python because it has zero libcamera Python
    binding requirements — it uses the same CLI tool that rpicam-hello
    calls under the hood.

"picamera2"
    Picamera2 Python library.  Works when the libcamera Python bindings
    match the active Python interpreter's ABI (e.g. system Python on the
    Pi, NOT Conda Python 3.10 against a system-compiled libcamera for
    Python 3.13).

"v4l2"
    cv2.VideoCapture('/dev/video0', cv2.CAP_V4L2) with MJPEG pixel
    format.  Useful when the libcamera V4L2 compatibility layer is active
    (sudo modprobe bcm2835-v4l2 on Bullseye, or libcamera-v4l2 package
    on Bookworm) and OpenCV's default VideoCapture fails to decode frames.

Choosing the right backend for Raspberry Pi + Conda Python 3.10
---------------------------------------------------------------
  CAMERA_BACKEND=libcamera   (recommended — auto-selects GStreamer or subprocess)
  CAMERA_BACKEND=libcamera_subprocess  (explicit, always works on Pi)

Do NOT use CAMERA_BACKEND=picamera2 unless your Conda Python version
matches the ABI of the system-installed libcamera Python bindings.

K_FOCAL recalibration after mounting Pi Camera Module 2
--------------------------------------------------------
Pi Camera Module 2 has a slightly wider FOV than a laptop webcam
(~67.5 deg diagonal vs ~60 deg), so K_FOCAL must be re-measured:

    1. Stand exactly 2.0 m from the mounted camera.
    2. Run with VERBOSE_DEBUG=1 to see face box dimensions in the overlay.
    3. Note median fw (face box width) and fh (face box height).
    4. New K_FOCAL = 2.0 * sqrt(fw * fh)
    5. Set K_FOCAL = <measured value> in config/settings.py.

The formula comes from the distance estimator in main.py:
    distance = K_FOCAL / sqrt(fw * fh)
Inverting at a known distance gives the calibration constant.
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Optional

import cv2
import numpy as np


# =============================================================================
# Utility: detect GStreamer support in this OpenCV build
# =============================================================================
def _opencv_has_gstreamer() -> bool:
    """Return True if this OpenCV build was compiled with GStreamer support."""
    try:
        info = cv2.getBuildInformation()
        for line in info.splitlines():
            stripped = line.strip()
            # Look for a line like "    GStreamer:                    YES (1.22.0)"
            if stripped.startswith("GStreamer") and "YES" in stripped:
                return True
    except Exception:
        pass
    return False


def _find_rpicam_tool() -> Optional[str]:
    """Return the first available rpicam-apps capture binary, or None."""
    for tool in ("rpicam-vid", "libcamera-vid"):
        try:
            result = subprocess.run(
                [tool, "--help"],
                capture_output=True, timeout=3
            )
            # rpicam-vid exits 0 or 1 on --help; either is fine as long as
            # it ran without FileNotFoundError.
            return tool
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


# =============================================================================
# Backend 1: OpenCV VideoCapture (laptop / USB webcam)
# =============================================================================
class OpenCVCamera:
    """
    cv2.VideoCapture wrapper.

    Suitable for laptop webcams and USB cameras.  Sets the requested
    resolution immediately after opening so YuNet receives the expected
    640×480 input without per-frame setInputSize calls.
    """

    def __init__(self, width: int = 640, height: int = 480) -> None:
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            raise RuntimeError(
                "OpenCVCamera: cv2.VideoCapture(0) failed to open. "
                "Ensure a USB webcam is connected, or switch to "
                "CAMERA_BACKEND=libcamera for Pi Camera Module 2."
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # Reduce internal buffer so we always get the latest frame.
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self) -> tuple[bool, np.ndarray]:
        """Return (True, bgr_frame) or (False, empty_array) on failure."""
        return self._cap.read()

    def release(self) -> None:
        self._cap.release()

    @property
    def is_open(self) -> bool:
        return self._cap.isOpened()


# =============================================================================
# Backend 2: GStreamer libcamerasrc (Pi — no Python ABI dependency)
# =============================================================================
class LibcameraGStreamerCamera:
    """
    Pi Camera Module 2 via the GStreamer libcamerasrc element.

    Bypasses all Python libcamera bindings entirely.  The camera is
    accessed through GStreamer's libcamerasrc C plugin, and frames are
    delivered to Python via OpenCV's GStreamer appsink bridge.

    Requirements
    ------------
    * OpenCV compiled with GStreamer support (check with
      cv2.getBuildInformation()).
    * GStreamer packages on the Pi:
        sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base
                         gstreamer1.0-plugins-good gstreamer1.0-plugins-bad

    The last package (plugins-bad) contains the libcamerasrc element.
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 15) -> None:
        if not _opencv_has_gstreamer():
            raise RuntimeError(
                "LibcameraGStreamerCamera: this OpenCV build does not have "
                "GStreamer support.  Switch to CAMERA_BACKEND=libcamera_subprocess "
                "or rebuild OpenCV with -DWITH_GSTREAMER=ON."
            )

        # capsfilter after libcamerasrc pins resolution + framerate.
        # videoconvert + BGR capsfilter produces the BGR24 output OpenCV needs.
        # appsink: drop=1 keeps only the latest frame (avoids growing queue).
        gst = (
            f"libcamerasrc ! "
            f"capsfilter caps='video/x-raw,width={width},height={height},"
            f"framerate={fps}/1' ! "
            f"videoconvert ! "
            f"capsfilter caps='video/x-raw,format=BGR' ! "
            f"appsink drop=1 max-buffers=1 sync=false"
        )
        self._cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        if not self._cap.isOpened():
            raise RuntimeError(
                "LibcameraGStreamerCamera: GStreamer pipeline failed to open.\n"
                "Diagnose with:\n"
                "  gst-launch-1.0 libcamerasrc ! videoconvert ! "
                "  video/x-raw,format=BGR ! autovideosink\n"
                "Common fixes:\n"
                "  sudo apt install gstreamer1.0-plugins-bad  "
                "(provides libcamerasrc)\n"
                "  rpicam-hello  # verify libcamera is functional"
            )

    def read(self) -> tuple[bool, np.ndarray]:
        return self._cap.read()

    def release(self) -> None:
        self._cap.release()

    @property
    def is_open(self) -> bool:
        return self._cap.isOpened()


# =============================================================================
# Backend 3: rpicam-vid subprocess + MJPEG pipe (Pi — always works)
# =============================================================================
class RPiCamSubprocessCamera:
    """
    Pi Camera Module 2 via a background rpicam-vid subprocess.

    rpicam-vid is the command-line camera application from rpicam-apps;
    it talks directly to the libcamera C library without any Python
    binding.  This backend works with ANY Python runtime (Conda Python
    3.10, system Python 3.13, pyenv, etc.) because it imposes zero
    Python/libcamera ABI constraints.

    Architecture
    ------------
    1. rpicam-vid writes a continuous MJPEG stream to stdout.
    2. A daemon reader thread reads chunks from the pipe, locates JPEG
       Start-of-Image (0xff 0xd8) and End-of-Image (0xff 0xd9) markers,
       and decodes each complete frame into a BGR numpy array.
    3. read() returns the most recently decoded frame (non-blocking after
       the first frame; blocking with a configurable timeout before that).

    The MJPEG codec was chosen over raw YUV420 because:
    - rpicam-vid reliably supports --codec mjpeg on all Pi OS versions.
    - JPEG decoding via cv2.imdecode is hardware-accelerated on Pi 4.
    - Frame boundaries are self-describing (SOI/EOI markers) so no fixed
      frame-size arithmetic is needed.

    Requirements
    ------------
    * rpicam-vid or libcamera-vid available in PATH (part of rpicam-apps,
      pre-installed on Pi OS Bookworm).

    Verify with:
        rpicam-hello --timeout 2000
    """

    _SOI = b"\xff\xd8"  # JPEG Start of Image
    _EOI = b"\xff\xd9"  # JPEG End of Image
    _READ_CHUNK = 65536  # bytes per stdout read call

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        fps: int = 15,
        startup_timeout_s: float = 10.0,
    ) -> None:
        tool = _find_rpicam_tool()
        if tool is None:
            raise RuntimeError(
                "RPiCamSubprocessCamera: neither 'rpicam-vid' nor "
                "'libcamera-vid' was found in PATH.  Install rpicam-apps:\n"
                "  sudo apt install rpicam-apps"
            )

        cmd = [
            tool,
            "--width",      str(width),
            "--height",     str(height),
            "--framerate",  str(fps),
            "--codec",      "mjpeg",
            "--nopreview",          # no display window (headless)
            "-t",           "0",    # run indefinitely
            "-o",           "-",    # output to stdout
        ]

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # suppress rpicam-vid console noise
            bufsize=0,                  # unbuffered stdout for minimum latency
        )

        self._frame:    Optional[np.ndarray] = None
        self._lock      = threading.Lock()
        self._running   = True
        self._first_frame_event = threading.Event()

        self._reader = threading.Thread(
            target=self._read_loop, name="rpicam-reader", daemon=True
        )
        self._reader.start()

        # Block until the first frame arrives or timeout expires.
        # This prevents the main pipeline from seeing False reads during
        # the ~1-2 s subprocess startup window.
        if not self._first_frame_event.wait(timeout=startup_timeout_s):
            self.release()
            raise RuntimeError(
                f"RPiCamSubprocessCamera: no frame received from {tool} "
                f"within {startup_timeout_s:.0f} s.\n"
                "Check that the Pi Camera Module 2 is connected and "
                "libcamera is functional:\n"
                f"  {tool} --timeout 2000 --nopreview"
            )

    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        """Background thread: continuously read stdout, parse JPEG frames."""
        buf = bytearray()
        while self._running:
            try:
                chunk = self._proc.stdout.read(self._READ_CHUNK)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf.extend(chunk)

            # Drain all complete JPEG frames from the accumulated buffer.
            while True:
                soi = buf.find(self._SOI)
                if soi < 0:
                    buf.clear()
                    break

                eoi = buf.find(self._EOI, soi + 2)
                if eoi < 0:
                    # Partial frame — keep from SOI, wait for more data.
                    del buf[:soi]
                    break

                jpeg_bytes = bytes(buf[soi : eoi + 2])
                del buf[: eoi + 2]

                arr   = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    with self._lock:
                        self._frame = frame
                    self._first_frame_event.set()

    # ------------------------------------------------------------------
    def read(self, timeout_s: float = 0.2) -> tuple[bool, Optional[np.ndarray]]:
        """
        Return (True, bgr_frame) or (False, None).

        Blocks for up to timeout_s waiting for a new frame from the
        background reader.  At 15 fps a frame arrives every ~67 ms, so
        200 ms gives three chances before declaring failure.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None:
                    return True, self._frame.copy()
            time.sleep(0.005)

        # Distinguish "subprocess died" from "temporarily slow"
        if self._proc.poll() is not None:
            return False, None
        # Subprocess still running but frame not yet updated — return stale
        with self._lock:
            if self._frame is not None:
                return True, self._frame.copy()
        return False, None

    # ------------------------------------------------------------------
    def release(self) -> None:
        self._running = False
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            try:
                self._proc.kill()
            except OSError:
                pass

    @property
    def is_open(self) -> bool:
        return self._proc.poll() is None


# =============================================================================
# Backend 4: Picamera2 Python library
# =============================================================================
class PiCamera2Camera:
    """
    Picamera2 wrapper for Pi Camera Module 2 via the libcamera stack.

    IMPORTANT: only works when the libcamera Python bindings were compiled
    for the SAME Python ABI as the active interpreter.  On Raspberry Pi OS
    Bookworm with Conda Python 3.10, picamera2 is installed under system
    Python 3.13, so the ABI does NOT match — use CAMERA_BACKEND=libcamera
    instead.

    Works correctly with system Python (e.g. `python3` from apt on Pi OS).
    """

    _BGR_FORMAT = "BGR888"
    _RGB_FORMAT = "RGB888"

    def __init__(self, width: int = 640, height: int = 480) -> None:
        try:
            from picamera2 import Picamera2  # lazy — Pi only
        except ImportError as exc:
            raise ImportError(
                "PiCamera2Camera: 'picamera2' is not importable in this "
                "Python environment.  If you are using Conda Python, the "
                "system-installed picamera2 was likely compiled for a "
                "different Python ABI.  Use CAMERA_BACKEND=libcamera instead."
                f"\nOriginal error: {exc}"
            ) from exc

        self._cam = Picamera2()
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
        frame = self._cam.capture_array("main")
        if not self._native_bgr:
            frame = frame[:, :, ::-1]  # RGB -> BGR (zero-copy view)
        return True, frame

    def release(self) -> None:
        try:
            self._cam.stop()
            self._cam.close()
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        return True


# =============================================================================
# Backend 5: V4L2 direct with MJPEG pixel format
# =============================================================================
class V4L2Camera:
    """
    cv2.VideoCapture with explicit V4L2 backend and MJPEG pixel format.

    Useful when:
    - The libcamera V4L2 compat module is loaded
      (sudo modprobe bcm2835-v4l2 on Bullseye, or libcamera-v4l2 package).
    - CAMERA_BACKEND=opencv succeeds in opening but read() returns False
      because OpenCV chose a pixel format the driver doesn't support.

    MJPEG is force-selected here because it is the most widely supported
    format by both the libcamera V4L2 bridge and USB webcam drivers.
    """

    def __init__(self, width: int = 640, height: int = 480) -> None:
        self._cap = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)
        if not self._cap.isOpened():
            raise RuntimeError(
                "V4L2Camera: /dev/video0 did not open.  "
                "Ensure the libcamera V4L2 compat module is loaded:\n"
                "  sudo modprobe bcm2835-v4l2"
            )
        # Force MJPEG to avoid YUYV/I420 format mismatches.
        self._cap.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc("M", "J", "P", "G"),
        )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self) -> tuple[bool, np.ndarray]:
        return self._cap.read()

    def release(self) -> None:
        self._cap.release()

    @property
    def is_open(self) -> bool:
        return self._cap.isOpened()


# =============================================================================
# Factory
# =============================================================================
def CameraSource(
    backend: str = "opencv",
    width: int = 640,
    height: int = 480,
    fps: int = 15,
):
    """
    Return a camera backend instance for the given CAMERA_BACKEND value.

    Parameters
    ----------
    backend : str
        "opencv"               — OpenCVCamera (laptop / USB webcam)
        "libcamera"            — auto-select GStreamer or subprocess (Pi)
        "libcamera_gstreamer"  — explicit GStreamer libcamerasrc pipeline
        "libcamera_subprocess" — explicit rpicam-vid subprocess
        "picamera2"            — Picamera2 Python library
        "v4l2"                 — explicit V4L2 with MJPEG pixel format

    width, height : int
        Capture resolution (default 640×480).

    fps : int
        Target frame rate for backends that accept it (libcamera backends).
        Ignored by OpenCV VideoCapture (which negotiates fps with the driver).

    Notes — Raspberry Pi with Conda Python
    ---------------------------------------
    Use CAMERA_BACKEND=libcamera (or libcamera_subprocess directly).
    cv2.VideoCapture(0) opens but read() fails because the libcamera V4L2
    bridge does not deliver frames to OpenCV's buffer API.  picamera2 fails
    due to a libcamera Python ABI mismatch between Conda Python 3.10 and
    the system-compiled bindings (Python 3.13).  Both libcamera backends
    bypass Python bindings entirely.
    """
    backend = backend.strip().lower()

    if backend == "opencv":
        return OpenCVCamera(width=width, height=height)

    if backend == "libcamera":
        # Auto-select: try GStreamer first (lower latency), fall back to
        # subprocess (always reliable).
        if _opencv_has_gstreamer():
            try:
                cam = LibcameraGStreamerCamera(width=width, height=height, fps=fps)
                print("[CAMERA] backend: libcamera_gstreamer (GStreamer libcamerasrc)")
                return cam
            except RuntimeError as exc:
                print(f"[CAMERA] GStreamer init failed: {exc}")
                print("[CAMERA] falling back to libcamera_subprocess")
        else:
            print("[CAMERA] OpenCV built without GStreamer; using libcamera_subprocess")
        return RPiCamSubprocessCamera(width=width, height=height, fps=fps)

    if backend == "libcamera_gstreamer":
        return LibcameraGStreamerCamera(width=width, height=height, fps=fps)

    if backend == "libcamera_subprocess":
        return RPiCamSubprocessCamera(width=width, height=height, fps=fps)

    if backend == "picamera2":
        return PiCamera2Camera(width=width, height=height)

    if backend == "v4l2":
        return V4L2Camera(width=width, height=height)

    raise ValueError(
        f"Unknown CAMERA_BACKEND '{backend}'. "
        "Valid values: 'opencv', 'libcamera', 'libcamera_gstreamer', "
        "'libcamera_subprocess', 'picamera2', 'v4l2'."
    )
