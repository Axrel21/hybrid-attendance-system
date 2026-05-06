# edge/main.py
# FIX-3: Removed 'from turtle import color, mode' which was polluting the namespace

import cv2
from enrollment.enroll import DATA_DIR
import numpy as np
import time
import csv
import json
import tensorflow as tf
from collections import deque
import os

from config import settings
from edge.tracker import HybridTracker
from edge.liveness import LivenessEngine
from edge.align import align_face
from edge.orientation import PoseEstimator
from edge.pipeline_controller import PipelineController
from edge.utils import is_valid_face

model_path1 = os.path.join(os.path.dirname(__file__), '..', 'models', 'yunet.onnx')
model_path2 = os.path.join(os.path.dirname(__file__), '..', 'models', 'mobilefacenet.tflite')
data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'known_faces.json')
log_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'attendance_log.csv')

def draw_debug_info(frame, x, y, info_lines, color):
    """ Draws multiple lines of text with spacing and a readable outline. """
    y_offset = y
    for line in info_lines:
        # Draw black outline
        cv2.putText(frame, line, (x, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 2)
        # Draw colored text
        cv2.putText(frame, line, (x, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        y_offset += 15

# ✅ NEW: IoU Calculation Helper
def calculate_iou(boxA, boxB):
    """ Calculates Intersection over Union (IoU) for two boxes in (x, y, w, h) format. """
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
    yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])

    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = boxA[2] * boxA[3]
    boxBArea = boxB[2] * boxB[3]

    return interArea / float(boxAArea + boxBArea - interArea + 1e-5)

# ✅ NEW: Robust Landmark/Face Matcher
def find_best_face_match(tracker_box, detected_faces, iou_threshold=0.3):
    """ Finds the detected face that best overlaps with the tracker's bounding box. """
    best_match = None
    max_iou = -1.0
    
    if detected_faces is None:
        return None

    for face in detected_faces:
        face_box = (int(face[0]), int(face[1]), int(face[2]), int(face[3]))
        iou = calculate_iou(tracker_box, face_box)
        
        if iou > max_iou:
            max_iou = iou
            best_match = face
            
    if max_iou >= iou_threshold:
        return best_match
        
    return None # Fallback if no good match is found (e.g., face occluded this frame)

class FinalHybridEdge:
    def __init__(self):
        # Configure OpenCV for strict edge simulation
        if settings.SIMULATE_PI:
            cv2.setNumThreads(settings.PI_MAX_THREADS)

        self.yunet = cv2.FaceDetectorYN.create(model_path1, "", (320, 240), 0.8, 0.3, 5000)
        
        self.interpreter = tf.lite.Interpreter(model_path=model_path2)
        if settings.SIMULATE_PI and hasattr(self.interpreter, "set_num_threads"):
            self.interpreter.set_num_threads(settings.PI_MAX_THREADS)
        self.interpreter.allocate_tensors()
        self.in_idx = self.interpreter.get_input_details()[0]['index']
        self.out_idx = self.interpreter.get_output_details()[0]['index']
        
        # Modules
        self.tracker = HybridTracker()
        self.liveness = LivenessEngine()
        self.pose_est = PoseEstimator()
        
        with open(data_path, 'r') as f:
            db = json.load(f)
        self.controller = PipelineController(db)
            
        self.embedding_buffers = {}
        self.cooldowns = {}
        
        # FIX-11: Check if log file exists to prevent writing header on every run
        log_exists = os.path.isfile(log_path) and os.path.getsize(log_path) > 0
        self.log_file = open(log_path, 'a', newline='')
        self.csv_writer = csv.writer(self.log_file)
        
        # FIX-11: Write header only if the file is newly created
        if not log_exists:
            self.csv_writer.writerow(["name", "confidence", "timestamp", "latency", 
                                      "liveness_label", "reason", "distance", "brightness", 
                                      "motion_score", "geometry_score", "mode", "track_id"])

    def extract_embedding(self, face_img):
        input_tensor = (np.float32(face_img) - 127.5) / 128.0
        input_tensor = np.expand_dims(input_tensor, axis=0)
        self.interpreter.set_tensor(self.in_idx, input_tensor)
        self.interpreter.invoke()
        emb = self.interpreter.get_tensor(self.out_idx)[0]
        return emb / np.linalg.norm(emb)

    def run(self):
        cap = cv2.VideoCapture(0)
        if settings.SIMULATE_PI:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            
        prev_gray = None

        while True:
            loop_start = time.time()
            ret, frame = cap.read()
            if not ret: break

            h, w = frame.shape[:2]
            curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # --- START DETECTION DEBUG BLOCK ---
            h, w = frame.shape[:2]
            
            # 1. Force exact input size match
            self.yunet.setInputSize((int(w), int(h)))
            
            # 2. Force low thresholds for 320x240 resolution
            self.yunet.setScoreThreshold(0.50)  
            self.yunet.setNMSThreshold(0.30)
            
            # 3. Detect (Ensure we are passing the BGR 'frame', NOT 'curr_gray')
            # ... (Inside the while True loop)
            _, faces = self.yunet.detect(frame)
            
            # 4. Print raw debug data to console
            print(f"[DEBUG] Frame: {w}x{h}, Channels: {frame.shape[2] if len(frame.shape)>2 else 1}")
            print(f"[DEBUG] Raw faces output type: {type(faces)}")
            print(f"[DEBUG] Faces found: {0 if faces is None else len(faces)}")

            rects = []
            valid_faces = [] # 🟢 ADD THIS: Keep track of the full face data that passed validation

            if faces is not None:
                for f in faces:
                    rx, ry, rw, rh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
                    
                    # --- FACE VALIDATION GATE ---
                    crop = frame[max(0, ry):ry+rh, max(0, rx):rx+rw]
                    raw_landmarks = [(int(f[4+2*j]), int(f[4+2*j+1])) for j in range(5)]
                    
                    if not is_valid_face(crop, raw_landmarks, (rx, ry, rw, rh), w, h):
                        cv2.rectangle(frame, (rx, ry), (rx+rw, ry+rh), (100, 100, 100), 1)
                        continue 
                    # ---------------------------------
                    
                    cv2.rectangle(frame, (rx, ry), (rx+rw, ry+rh), (255, 0, 0), 1)
                    rects.append((rx, ry, rw, rh))
                    valid_faces.append(f) # 🟢 ADD THIS: Store the valid raw face data
                    
            objects = self.tracker.update(rects)

            for track_id, (centroid, box) in objects.items():
                x, y, fw, fh = box
                
                # 🟢 MODIFY THIS: Pass valid_faces instead of raw faces
                matched_face = find_best_face_match(box, valid_faces, iou_threshold=0.3)
                
                if matched_face is None:
                    self.embedding_buffers.pop(track_id, None)
                    self.liveness.history.pop(track_id, None)
                    continue
                
                # Extract landmarks safely
                landmarks = [(int(matched_face[4+2*j]), int(matched_face[4+2*j+1])) for j in range(5)]
                
                # ... [The rest of your pipeline (Pose, Liveness, Embeddings, Match) remains EXACTLY the same] ...
                mode = self.pose_est.estimate_mode(track_id, landmarks)
                
                distance = settings.K_FOCAL / (np.sqrt(fw * fh) + 1e-5)
                if not (settings.MIN_DISTANCE < distance < settings.MAX_DISTANCE): continue
                
                # FIX 4,5,6,7: Deep Temporal Liveness
                lbl, conf, rsn, m_score, g_score = self.liveness.assess_frame(
                    track_id, mode, prev_gray, frame, box, landmarks)
                    
                if lbl != "REAL":
                    self.embedding_buffers.pop(track_id, None)
                    continue

                if lbl == "REAL":
                    if track_id not in self.embedding_buffers:
                        self.embedding_buffers[track_id] = deque(maxlen=settings.LIVENESS_WINDOW)
                    
                    # FIX 1: 5-Point Alignment
                    bgr_crop = frame[max(0,y):y+fh, max(0,x):x+fw]
                    if bgr_crop.size > 0:
                        local_landmarks = [(lx - x, ly - y) for lx, ly in landmarks]
                        aligned_face = align_face(bgr_crop, local_landmarks)
                        emb = self.extract_embedding(aligned_face)
                        self.embedding_buffers[track_id].append(emb)

                    if len(self.embedding_buffers[track_id]) >= settings.LIVENESS_WINDOW:
                        mean_emb = np.mean(self.embedding_buffers[track_id], axis=0)
                        mean_emb = mean_emb / np.linalg.norm(mean_emb)
                        
                        # FIX 9: Pose-Aware Matching
                        identity, sim = self.controller.pose_aware_match(mean_emb, mode)
                        
                        # FIX 8: Adaptive Thresholds
                        brightness = np.mean(curr_gray[y:y+fh, x:x+fw])
                        th_high, th_mid = self.controller.get_adaptive_threshold(brightness, distance, mode=="OVERHEAD")
                        
                        if sim >= th_high:
                            if time.time() - self.cooldowns.get(identity, 0) > 300:
                                self.cooldowns[identity] = time.time()
                                print(f"ATTENDANCE MARKED: {identity}")
                        elif sim >= th_mid:
                            print("OFFLOADING TO ARCFACE SERVER...")

                        # FIX 11: Comprehensive Logging
                        total_latency = (time.time() - loop_start) * 1000
                        self.csv_writer.writerow([
                            identity, round(sim, 3), time.time(), round(total_latency, 1),
                            lbl, rsn, round(distance, 2), round(brightness, 1),
                            round(m_score, 2), round(g_score, 2), mode, track_id
                        ])

                # ==========================================
                # 🛑 UI & DEBUG VISUALIZATION OVERLAY 🛑
                # ==========================================
                
                # 1. Safe Variable Handling (Defaults if undefined)
                safe_lbl = locals().get("lbl", "UNKNOWN")
                safe_conf = locals().get("sim", locals().get("conf", 0.0))
                safe_mode = locals().get("mode", "NA")
                safe_dist = locals().get("distance", 0.0)
                safe_bright = locals().get("brightness", 0.0)
                safe_m_score = locals().get("m_score", 0.0)
                safe_g_score = locals().get("g_score", 0.0)
                safe_rsn = locals().get("rsn", "N/A")

                # 2. Determine Bounding Box Color
                if safe_lbl == "REAL":
                    color = (0, 255, 0)      # Green
                elif safe_lbl == "SPOOF":
                    color = (0, 0, 255)      # Red
                elif safe_lbl == "UNCERTAIN":
                    color = (0, 255, 255)    # Yellow
                else:
                    color = (200, 200, 200)  # Gray fallback

                # 3. Format Information Lines
                info_lines = [
                    f"ID: {track_id} | Mode: {safe_mode}",
                    f"Live: {safe_lbl} ({safe_conf:.2f})",
                    f"Dist: {safe_dist:.1f}m | Bright: {safe_bright:.0f}",
                    f"Motion: {safe_m_score:.2f} | Geom: {safe_g_score:.2f}",
                    f"Status: {safe_rsn}"
                ]

                # 4. Draw Single Bounding Box
                cv2.rectangle(frame, (x, y), (x+fw, y+fh), color, 2)

                # 5. Draw Debug Info using Helper Function
                draw_debug_info(frame, x + fw + 8, max(20, y), info_lines, color)
                
            prev_gray = curr_gray.copy()
            cv2.imshow("Hybrid Edge Pipeline", frame)
            
            # FIX 13: Accurate Pi Simulation Logic
            if settings.SIMULATE_PI:
                elapsed = (time.time() - loop_start) * 1000
                sleep_time = max(1, int(settings.TARGET_LATENCY_MS - elapsed))
                if cv2.waitKey(sleep_time) & 0xFF == ord('q'): break
            else:
                if cv2.waitKey(1) & 0xFF == ord('q'): break

            # FIX-2: Deleted duplicate sleep_time computation and cv2.waitKey block here

        cap.release()
        cv2.destroyAllWindows()
        self.log_file.close()

if __name__ == "__main__":
    node = FinalHybridEdge()
    node.run()

# FIX-1: Deleted the trailing dead code block containing undefined references and execution outside the class scope