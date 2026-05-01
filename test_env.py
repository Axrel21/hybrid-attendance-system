import cv2
import tensorflow as tf

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
    yunet = cv2.FaceDetectorYN.create('models/yunet.onnx', "", (320, 240))
    print("✅ YuNet Loaded OK")
except Exception as e:
    print(f"❌ YuNet Error: {e}")

# 3. Test TFLite Loading
try:
    interpreter = tf.lite.Interpreter(model_path='models/mobilefacenet.tflite')
    interpreter.allocate_tensors()
    print("✅ TFLite MobileFaceNet Loaded OK")
except Exception as e:
    print(f"❌ TFLite Error: {e}")
    