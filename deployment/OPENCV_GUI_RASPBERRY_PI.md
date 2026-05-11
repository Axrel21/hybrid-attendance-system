# OpenCV HighGUI (GTK) on Raspberry Pi 4

This project can run with a **native OpenCV window** (`cv2.imshow`) when `HEADLESS=0`. That requires a **GUI-capable** OpenCV build, not `opencv-python-headless`.

## 1. System packages (Bookworm / Debian)

Install GTK and build helpers so the PyPI `opencv-python` wheel can use HighGUI:

```bash
sudo apt update
sudo apt install -y \
  libgtk-3-0 libgtk-3-dev \
  libcanberra-gtk3-module \
  libgl1-mesa-dri \
  pkg-config
```

For minimal images, also ensure a display session exists (HDMI, desktop, or VNC) and `DISPLAY` is set if you SSH without `-X`.

## 2. Python: switch from headless to GUI wheel

Inside your project virtualenv:

```bash
pip uninstall -y opencv-python opencv-python-headless
pip install 'opencv-python==4.8.0.76'
```

Align the version with `requirements_pi.txt` comments (or pin the same minor as your team standard).

## 3. Validate before running the full pipeline

```bash
source ~/attendance/venv/bin/activate
cd /path/to/edge_implementation
python deployment/validate_opencv_gui.py
```

You should see a line like `HighGUI backend: GTK+`, not `GUI: NONE`.

## 4. Run in GUI vs headless mode

| Mode | Environment | Behavior |
|------|-------------|----------|
| GUI | `HEADLESS=0` | `cv2.namedWindow` / `imshow` / `waitKey`; overlays on frame |
| Headless | `HEADLESS=1` | No GUI calls; loop uses sleep pacing |

Optional remote MJPEG (does **not** replace the native window):

```bash
HEADLESS=1 STREAM_VIDEO=1 python run.py
# Browser: http://<pi-ip>:5000/video_feed
```

## 5. Troubleshooting

- **`error: (-2:Unspecified error) The function is not implemented` on `imshow`:**  
  Still on `opencv-python-headless` or missing GTK libs — repeat sections 1–2.

- **SSH with no display:**  
  Use `HEADLESS=1`, or `ssh -X`, or enable VNC / local monitor.

- **Bypass build-info precheck (advanced):**  
  `SKIP_OPENCV_GUI_CHECK=1 HEADLESS=0 python run.py` — only if you know HighGUI works.

## 6. Benchmarking note

For lowest display overhead and most accurate latency/FPS telemetry, prefer **`HEADLESS=0` on a local HDMI / desktop session** and avoid browser streaming unless you need remote observation.
