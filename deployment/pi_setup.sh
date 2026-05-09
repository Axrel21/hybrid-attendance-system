#!/usr/bin/env bash
# deployment/pi_setup.sh
# ============================================================
# Raspberry Pi 4 — runtime validation / smoke test script
# ============================================================

set -euo pipefail

PROJECT_DIR="$HOME/projects/edge-facial-recognition-pipeline"

echo "=================================================="
echo "  Attendance Pipeline — Pi Setup"
echo "  Project: $PROJECT_DIR"
echo "=================================================="

# --- Smoke tests ---------------------------------------------------------
echo "[1] Running compatibility smoke tests..."

echo "  [1a] tflite-runtime interpreter..."
python - <<'PYEOF'
try:
    from tflite_runtime.interpreter import Interpreter
    print("       OK: tflite_runtime.interpreter.Interpreter available")
except ImportError as e:
    print(f"       FAIL: {e}")
    raise SystemExit(1)
PYEOF

echo "  [1b] cv2.FaceDetectorYN..."
python - <<'PYEOF'
import cv2

print(f"       cv2 version: {cv2.__version__}")

if not hasattr(cv2, 'FaceDetectorYN'):
    print("       FAIL: cv2.FaceDetectorYN not found")
    raise SystemExit(1)

print("       OK: cv2.FaceDetectorYN available")
PYEOF

echo "  [1c] psutil..."
python - <<'PYEOF'
import psutil
print(f"       OK: psutil {psutil.__version__}")
PYEOF

echo "  [1d] MobileFaceNet TFLite model loads..."
python - <<PYEOF
import os
import sys

sys.path.insert(0, "$PROJECT_DIR")

model_path = os.path.join(
    "$PROJECT_DIR",
    "models",
    "mobilefacenet.tflite"
)

try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    import tensorflow as tf
    Interpreter = tf.lite.Interpreter

interp = Interpreter(model_path=model_path)
interp.allocate_tensors()

out_shape = interp.get_output_details()[0]['shape']

print(f"       Model output shape: {out_shape}")
print("       OK: MobileFaceNet loads correctly")
PYEOF

echo ""
echo "=================================================="
echo "  Setup complete."
echo "  Activate environment:"
echo "    conda activate edgepi"
echo ""
echo "  Run manually:"
echo "    python run.py"
echo "=================================================="
