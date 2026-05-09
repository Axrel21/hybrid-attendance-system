#!/usr/bin/env bash
# deployment/pi_setup.sh
# ============================================================
# Raspberry Pi 4 — one-shot environment setup script
# Run this on the Pi after cloning / rsyncing the project.
# ============================================================
set -euo pipefail

PROJECT_DIR="$HOME/attendance"
VENV_DIR="$PROJECT_DIR/venv"
PYTHON=python3

echo "=================================================="
echo "  Attendance Pipeline — Pi Setup"
echo "  Project: $PROJECT_DIR"
echo "=================================================="

# --- 1. System packages --------------------------------------------------
echo "[1] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    libatlas-base-dev \
    libopenblas-dev \
    libjpeg-dev \
    libpng-dev \
    libcamera-dev \
    python3-libcamera \
    python3-picamera2 \
    --no-install-recommends

# --- 2. Virtualenv -------------------------------------------------------
echo "[2] Creating virtualenv at $VENV_DIR..."
$PYTHON -m venv --system-site-packages "$VENV_DIR"
# --system-site-packages pulls in the apt-installed picamera2 which links
# against the system libcamera. A pure pip install of picamera2 may fail
# on some Bookworm builds due to native library ABI requirements.

# --- 3. Pip packages -----------------------------------------------------
echo "[3] Installing pip packages..."
"$VENV_DIR/bin/pip" install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements_pi.txt"

# --- 4. Smoke tests ------------------------------------------------------
echo "[4] Running compatibility smoke tests..."

echo "  [4a] tflite-runtime interpreter..."
"$VENV_DIR/bin/python" - <<'PYEOF'
try:
    from tflite_runtime.interpreter import Interpreter
    print("       OK: tflite_runtime.interpreter.Interpreter available")
except ImportError as e:
    print(f"       FAIL: {e}")
    raise SystemExit(1)
PYEOF

echo "  [4b] cv2.FaceDetectorYN..."
"$VENV_DIR/bin/python" - <<'PYEOF'
import cv2
print(f"       cv2 version: {cv2.__version__}")
if not hasattr(cv2, 'FaceDetectorYN'):
    print("       FAIL: cv2.FaceDetectorYN not found — rebuild or upgrade opencv")
    raise SystemExit(1)
print("       OK: cv2.FaceDetectorYN available")
PYEOF

echo "  [4c] picamera2 import..."
"$VENV_DIR/bin/python" - <<'PYEOF'
try:
    from picamera2 import Picamera2
    print("       OK: Picamera2 available")
except ImportError as e:
    print(f"       WARN: picamera2 not available ({e})")
    print("       If using CAMERA_BACKEND=opencv this is non-fatal.")
PYEOF

echo "  [4d] psutil..."
"$VENV_DIR/bin/python" - <<'PYEOF'
import psutil
print(f"       OK: psutil {psutil.__version__}")
PYEOF

echo "  [4e] MobileFaceNet TFLite model loads and produces (1,128) output..."
"$VENV_DIR/bin/python" - <<PYEOF
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
assert list(out_shape) == [1, 128], f"Expected [1,128] got {out_shape}"
print("       OK: MobileFaceNet loads correctly")
PYEOF

# --- 5. Optional: systemd service ----------------------------------------
echo ""
echo "[5] Systemd service (optional — run manually if desired):"
echo "    sudo cp $PROJECT_DIR/deployment/attendance.service /etc/systemd/system/"
echo "    sudo systemctl daemon-reload"
echo "    sudo systemctl enable attendance"
echo "    sudo systemctl start attendance"

echo ""
echo "=================================================="
echo "  Setup complete. Activate the venv with:"
echo "    source $VENV_DIR/bin/activate"
echo "  Run manually:"
echo "    python run.py"
echo "  Or start the service (if installed above)."
echo "=================================================="
