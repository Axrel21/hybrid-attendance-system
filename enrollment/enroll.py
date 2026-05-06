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

    # FIX-9: Adding a default pose parameter. If unknown, we replicate embeddings into both arrays.
    def enroll_user(self, name, image_paths, pose="frontal"):
        self.db[name] = {"frontal": [], "angled": []}
        for path in image_paths:
            img = cv2.imread(path)
            h, w = img.shape[:2]
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(img)
            
            if faces is not None:
                box = list(map(int, faces[0][:4]))
                # Crop and embed (Simplified here, assumes alignment is done)
                crop = img[box[1]:box[1]+box[3], box[0]:box[0]+box[2]]
                emb = self.extract_embedding(crop)
                
                # FIX-9: Populating both frontal and angled arrays so pipeline matching 
                # (which checks 'angled' by default on overhead cameras) never queries an empty list.
                if pose == "frontal":
                    self.db[name]["frontal"].append(emb)
                    self.db[name]["angled"].append(emb)
                else:
                    self.db[name]["angled"].append(emb)

        with open('../data/known_faces.json', 'w') as f:
            json.dump(self.db, f)
        print(f"Enrolled {name} with {len(image_paths)} embeddings.")

if __name__ == "__main__":
    # Example usage
    tflite_path = os.path.join(MODEL_DIR, 'mobilefacenet.tflite')
    yunet_path = os.path.join(MODEL_DIR, 'yunet.onnx')
    enroller = Enroller(tflite_path, yunet_path)
    # enroller.enroll_user("john_doe", ["img1.jpg", "img2.jpg", "img3.jpg", "img4.jpg", "img5.jpg"])