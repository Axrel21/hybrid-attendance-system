"""
Standalone Flask MJPEG stream for the edge pipeline.

Thread model: one lock-protected slot holds the newest JPEG bytes. The capture
thread calls set_frame(); browser clients read the same bytes from /video_feed.

When config.settings.STREAM_VIDEO is true, edge.main pushes each composed frame
here (optional; native GUI remains primary).
"""

from __future__ import annotations

import threading
import time
from typing import Generator, Optional

import cv2
import numpy as np
from flask import Flask, Response

# --- Latest-frame buffer (single slot, no queue) --------------------------------

_lock = threading.Lock()
_jpeg_bytes: Optional[bytes] = None

# MJPEG multipart boundary (must not appear inside JPEG payload; practically fine)
_MJPEG_BOUNDARY = b"frame"

# Default encode settings tuned for Pi + browser preview (640x480 typical)
_DEFAULT_JPEG_QUALITY = 75  # recommended range 70–80

_flask_app = Flask(__name__)


def set_frame(frame: np.ndarray, jpeg_quality: int = _DEFAULT_JPEG_QUALITY) -> None:
    """
    Store the newest frame for streaming; replaces any previous frame.

    Call this from your inference loop with the final annotated BGR image.
    Encoding happens here once per update so clients do not trigger extra
    cv2.imencode work. Only the latest JPEG bytes are kept under the lock.

    Args:
        frame: BGR image (e.g. from OpenCV). Empty or invalid frames are ignored.
        jpeg_quality: 0–100; 70–80 is a good balance for Pi 4 and Wi‑Fi.
    """
    global _jpeg_bytes
    if frame is None or frame.size == 0:
        return
    if jpeg_quality < 1:
        jpeg_quality = 1
    elif jpeg_quality > 100:
        jpeg_quality = 100

    ok, buf = cv2.imencode(
        ".jpg",
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)],
    )
    if not ok or buf is None:
        return

    # Own immutable bytes so the array from imencode can be released; one copy.
    payload = buf.tobytes()

    with _lock:
        _jpeg_bytes = payload


def _snapshot_jpeg() -> Optional[bytes]:
    """Return a reference to the latest JPEG under lock (no copy of image bytes)."""
    with _lock:
        return _jpeg_bytes


def generate_mjpeg(
    fps_cap: float = 30.0,
    idle_sleep_s: float = 0.02,
) -> Generator[bytes, None, None]:
    """
    Multipart MJPEG generator for Flask Response.

    Repeatedly emits the current JPEG with a multipart boundary. Sleep limits
    CPU when no new frame is available and caps stream rate. Always re-reads
    the slot after sleep so stale in-memory references are not held across
    waits — only the newest blob from set_frame is ever served.

    Args:
        fps_cap: Soft ceiling on parts per second (sleep derived from it).
        idle_sleep_s: Sleep when there is no frame yet to avoid a tight loop.
    """
    period = 1.0 / fps_cap if fps_cap > 0 else 0.0
    boundary = b"--" + _MJPEG_BOUNDARY + b"\r\n"
    ctype = b"Content-Type: image/jpeg\r\n\r\n"

    while True:
        t0 = time.monotonic()
        data = _snapshot_jpeg()
        if data:
            yield boundary + ctype + data + b"\r\n"
        else:
            time.sleep(idle_sleep_s)
            continue

        # Pace the loop: do not busy-spin; also pick up fresh frames between ticks.
        elapsed = time.monotonic() - t0
        if period > elapsed:
            time.sleep(period - elapsed)


@_flask_app.route("/video_feed")
def video_feed() -> Response:
    """MJPEG stream; browsers and OpenCV can open this URL as a motion JPEG source."""
    return Response(
        generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=" + _MJPEG_BOUNDARY.decode("ascii"),
    )


@_flask_app.route("/")
def index() -> str:
    """Minimal HTML page that embeds the live stream."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Edge camera</title>
  <style>
    body { font-family: sans-serif; margin: 1rem; background: #111; color: #eee; }
    img { max-width: 100%; height: auto; border: 1px solid #444; }
  </style>
</head>
<body>
  <h1>Live preview</h1>
  <img src="/video_feed" alt="MJPEG stream"/>
</body>
</html>
"""


def start_stream_server(
    host: str = "0.0.0.0",
    port: int = 5000,
    daemon: bool = True,
    **flask_run_kwargs,
) -> threading.Thread:
    """
    Run the Flask app in a background thread.

    Args:
        host: 0.0.0.0 listens on all interfaces (reachable from laptop/phone on LAN).
        port: TCP port (ensure firewall allows it if needed).
        daemon: If True, thread exits when the main program exits.
        **flask_run_kwargs: forwarded to app.run (e.g. threaded=True is set inside).

    Returns:
        The started threading.Thread (already running).
    """
    if flask_run_kwargs.pop("use_reloader", None) is True:
        raise ValueError("use_reloader must stay False when running in a thread")

    def _run() -> None:
        import logging

        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        _flask_app.run(
            host=host,
            port=port,
            threaded=True,
            use_reloader=False,
            **flask_run_kwargs,
        )

    th = threading.Thread(target=_run, name="mjpeg-flask", daemon=daemon)
    th.start()
    return th


# Public app handle for tests or custom WSGI deployment
app = _flask_app
