# enrollment/enroll.py
import cv2
import numpy as np
import json
import os
import tensorflow as tf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, '..', 'models')
DATASET_DIR = os.path.join(BASE_DIR, '..', 'dataset')
DATA_DIR = os.path.join(BASE_DIR, '..', 'data')

class Enroller:
    def __init__(self, tflite_path, yunet_path):
        # Initialize YuNet
        self.detector = cv2.FaceDetectorYN.create(
            yunet_path, "", (320, 320), 0.9, 0.3, 5000
        )
        # Initialize MobileFaceNet TFLite
        self.interpreter = tf.lite.Interpreter(model_path=tflite_path)
        self.interpreter.allocate_tensors()
        self.input_idx = self.interpreter.get_input_details()[0]['index']
        self.output_idx = self.interpreter.get_output_details()[0]['index']
        self.db = {}

    def extract_embedding(self, face_crop):
        face_crop = cv2.resize(face_crop, (112, 112))
        input_tensor = (np.float32(face_crop) - 127.5) / 128.0
        input_tensor = np.expand_dims(input_tensor, axis=0)
        self.interpreter.set_tensor(self.input_idx, input_tensor)
        self.interpreter.invoke()
        embedding = self.interpreter.get_tensor(self.output_idx)[0]
        return (embedding / np.linalg.norm(embedding)).tolist() # Normalize L2

    def enroll_user(self, name, image_paths):
        self.db[name] = {"frontal": [], "angled": []}
        valid_count = 0
        
        for path in image_paths:
            img = cv2.imread(path)
            if img is None:
                print(f"    [WARNING] Failed to load image: {path}")
                continue

            h, w = img.shape[:2]
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(img)
            
            if faces is not None:
                box = list(map(int, faces[0][:4]))
                
                # Safe crop with boundary checks
                x, y, fw, fh = box[0], box[1], box[2], box[3]
                crop = img[max(0, y):y+fh, max(0, x):x+fw]
                
                if crop.size == 0:
                    print(f"    [WARNING] Invalid crop dimensions for: {path}")
                    continue
                    
                emb = self.extract_embedding(crop)
                self.db[name]["frontal"].append(emb)
                valid_count += 1
                print(f"    [SUCCESS] Extracted embedding from {os.path.basename(path)}")
            else:
                print(f"    [WARNING] No face detected in: {path}")

        # Safe directory creation and path saving
        os.makedirs(DATA_DIR, exist_ok=True)
        json_path = os.path.join(DATA_DIR, 'known_faces.json')
        
        with open(json_path, 'w') as f:
            json.dump(self.db, f)
            
        print(f"  ✅ Enrolled {name} with {valid_count}/{len(image_paths)} valid embeddings.")


if __name__ == "__main__":
    tflite_path = os.path.join(MODEL_DIR, 'mobilefacenet.tflite')
    
    # Ensure this string exactly matches the file name in your models folder!
    yunet_path = os.path.join(MODEL_DIR, 'yunet.onnx') 
    
    enroller = Enroller(tflite_path, yunet_path)
    
    print(f"Scanning dataset directory: {DATASET_DIR}")
    
    if not os.path.exists(DATASET_DIR):
        print(f"[ERROR] Dataset directory not found: {DATASET_DIR}")
    else:
        # Detect all folders in the dataset directory
        users = [d for d in os.listdir(DATASET_DIR) if os.path.isdir(os.path.join(DATASET_DIR, d))]
        print(f"Found {len(users)} user folders: {users}\n")
        
        for user_name in users:
            user_dir = os.path.join(DATASET_DIR, user_name)
            
            # Collect all image paths for this user
            image_paths = [
                os.path.join(user_dir, f) for f in os.listdir(user_dir) 
                if f.lower().endswith(('.png', '.jpg', '.jpeg'))
            ]
            
            if not image_paths:
                print(f"[SKIPPING] No images found for user: {user_name}")
                continue
                
            print(f"Processing user: {user_name} ({len(image_paths)} images found)...")
            enroller.enroll_user(user_name, image_paths)
            
        print(f"\n🎉 Enrollment Complete! Data saved to: {os.path.join(DATA_DIR, 'known_faces.json')}")