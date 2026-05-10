#!/usr/bin/env bash
# deployment/pi_setup.sh
# ============================================================
# Raspberry Pi 4 — Conda Python 3.10 environment smoke tests
# and camera backend validation.
#
# Assumes:
#   - Conda / Miniforge is installed
#   - The Conda env is already activated (e.g. "conda activate edgepi")
#   - The project is cloned to $PROJECT_DIR
#
# Run AFTER activating the Conda env:
#   conda activate edgepi
#   bash deployment/pi_setup.sh
# ============================================================
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/projects/edge-facial-recognition-pipeline}"

echo "=================================================="
echo "  Attendance Pipeline — Pi Smoke Tests"
echo "  Project : $PROJECT_DIR"
echo "  Python  : $(python --version)"
echo "=================================================="

# ---- 1. Core inference stack ----------------------------------------
echo ""
echo "[1] Core inference stack..."

echo "  [1a] tflite-runtime..."
python - <<'PYEOF'
try:
    from tflite_runtime.interpreter import Interpreter
    print("       OK: tflite_runtime available")
except ImportError as e:
    print(f"       FAIL: {e}")
    raise SystemExit(1)
PYEOF

echo "  [1b] OpenCV + FaceDetectorYN..."
python - <<'PYEOF'
import cv2
print(f"       cv2 version: {cv2.__version__}")
if not hasattr(cv2, 'FaceDetectorYN'):
    print("       FAIL: cv2.FaceDetectorYN missing — upgrade opencv-python")
    raise SystemExit(1)
print("       OK: cv2.FaceDetectorYN present")
PYEOF

echo "  [1c] psutil..."
python - <<'PYEOF'
import psutil
print(f"       OK: psutil {psutil.__version__}")
PYEOF

echo "  [1d] MobileFaceNet TFLite (192D output expected)..."
python - <<PYEOF
import os, sys
sys.path.insert(0, "$PROJECT_DIR")
model_path = os.path.join("$PROJECT_DIR", "models", "mobilefacenet.tflite")
try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    import tensorflow as tf
    Interpreter = tf.lite.Interpreter
interp = Interpreter(model_path=model_path)
interp.allocate_tensors()
out_shape = interp.get_output_details()[0]['shape']
print(f"       Model output shape: {out_shape}")
# Accept both 128-D (original) and 192-D (quantised) builds
assert out_shape[0] == 1 and out_shape[1] in (128, 192), f"Unexpected shape {out_shape}"
print("       OK: MobileFaceNet loads correctly")
PYEOF

# ---- 2. OpenCV GStreamer support check ------------------------------
echo ""
echo "[2] OpenCV GStreamer support..."
python - <<'PYEOF'
import cv2
info = cv2.getBuildInformation()
gstreamer_ok = any(
    "GStreamer" in line and "YES" in line
    for line in info.splitlines()
)
if gstreamer_ok:
    print("       OK: this OpenCV build includes GStreamer support")
    print("       CAMERA_BACKEND=libcamera will prefer libcamera_gstreamer")
else:
    print("       INFO: this OpenCV build does NOT have GStreamer support")
    print("       CAMERA_BACKEND=libcamera will use libcamera_subprocess instead")
    print("       (this is fine — subprocess backend is equally reliable)")
PYEOF

# ---- 3. Camera tool availability ------------------------------------
echo ""
echo "[3] libcamera / rpicam-apps tools..."

# Check rpicam-vid (Bookworm) or libcamera-vid (Bullseye)
CAM_TOOL=""
for tool in rpicam-vid libcamera-vid; do
    if command -v "$tool" &>/dev/null; then
        CAM_TOOL="$tool"
        echo "       OK: $tool found at $(command -v $tool)"
        break
    fi
done

if [ -z "$CAM_TOOL" ]; then
    echo "       WARN: neither rpicam-vid nor libcamera-vid found."
    echo "       Install with: sudo apt install rpicam-apps"
    echo "       CAMERA_BACKEND=libcamera_subprocess will not work without it."
else
    # Quick libcamera functional check (2-second preview, no display)
    echo "       Testing libcamera capture (2 s, no preview)..."
    if $CAM_TOOL --timeout 2000 --nopreview 2>/dev/null; then
        echo "       OK: libcamera capture works"
    else
        echo "       WARN: $CAM_TOOL exited non-zero — check Pi Camera cable"
    fi
fi

# ---- 4. GStreamer libcamerasrc element (optional) -------------------
echo ""
echo "[4] GStreamer libcamerasrc element (optional, used by libcamera_gstreamer)..."
if command -v gst-inspect-1.0 &>/dev/null; then
    if gst-inspect-1.0 libcamerasrc &>/dev/null 2>&1; then
        echo "       OK: libcamerasrc GStreamer element is available"
    else
        echo "       INFO: libcamerasrc not found in GStreamer registry"
        echo "       Install with: sudo apt install gstreamer1.0-plugins-bad"
        echo "       (not required — libcamera_subprocess works without it)"
    fi
else
    echo "       INFO: gst-inspect-1.0 not found; GStreamer not installed"
    echo "       Install with: sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-bad"
fi

# ---- 5. Pipeline module import test ---------------------------------
echo ""
echo "[5] Pipeline module import test..."
python - <<PYEOF
import sys
sys.path.insert(0, "$PROJECT_DIR")
from config import settings
from edge.camera import CameraSource, _opencv_has_gstreamer, _find_rpicam_tool
from edge.main import FinalHybridEdge, DIAG_COLUMNS
print(f"       OK: all pipeline modules import cleanly")
print(f"       DIAG schema: {len(DIAG_COLUMNS)} columns")
print(f"       CAMERA_BACKEND setting: {settings.CAMERA_BACKEND!r}")
print(f"       HEADLESS setting: {settings.HEADLESS}")
has_gst = _opencv_has_gstreamer()
tool = _find_rpicam_tool()
print(f"       OpenCV GStreamer: {has_gst}")
print(f"       rpicam tool: {tool}")
if has_gst:
    print("       => libcamera will use: libcamera_gstreamer")
elif tool:
    print(f"       => libcamera will use: libcamera_subprocess ({tool})")
else:
    print("       => WARNING: no libcamera backend available!")
PYEOF

echo ""
echo "=================================================="
echo "  Smoke tests complete."
echo ""
echo "  Recommended run command (headless, Pi Camera):"
echo "    CAMERA_BACKEND=libcamera HEADLESS=1 VERBOSE_DEBUG=0 python run.py"
echo ""
echo "  Or with explicit subprocess backend:"
echo "    CAMERA_BACKEND=libcamera_subprocess HEADLESS=1 python run.py"
echo ""
echo "  Orientation calibration session:"
echo "    CAMERA_BACKEND=libcamera HEADLESS=1 \\"
echo "    python -m experiments.run_orientation_experiment pi_frontal_2m \\"
echo "        --notes 'stand 2m, measure K_FOCAL' --quiet"
echo "=================================================="
