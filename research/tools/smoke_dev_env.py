import os

import cv2
try:
    from tflite_runtime.interpreter import Interpreter
    BACKEND = "tflite-runtime"
except ImportError:
    import tensorflow as tf
    Interpreter = tf.lite.Interpreter
    BACKEND = "tensorflow"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MODELS = os.path.join(_ROOT, "models")

# 1. Test GUI & Camera
cap = cv2.VideoCapture(0)
ret, frame = cap.read()
if ret:
    cv2.imshow("GUI Test", frame)
    cv2.waitKey(1000)
cap.release()
cv2.destroyAllWindows()
print("✅ Camera & GUI OK")

# 2. Test YuNet Loading
try:
    yunet = cv2.FaceDetectorYN.create(
        os.path.join(_MODELS, "yunet.onnx"), "", (320, 240)
    )
    print("✅ YuNet Loaded OK")
except Exception as e:
    print(f"❌ YuNet Error: {e}")

# 3. Test TFLite Loading
try:
    interpreter = Interpreter(
        model_path=os.path.join(_MODELS, "mobilefacenet.tflite")
    )
    interpreter.allocate_tensors()
    print("✅ TFLite MobileFaceNet Loaded OK")
except Exception as e:
    print(f"❌ TFLite Error: {e}")
    
