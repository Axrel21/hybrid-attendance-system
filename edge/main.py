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
diag_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'diagnostic_log.csv')

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

# ✅ NEW: Pre-tracker suppression of nested / duplicate YuNet detections.
def suppress_overlapping(faces, iou_th=0.45, iomin_th=0.70):
    """
    Greedy post-pass over YuNet output.

    Standard IoU-NMS (which YuNet already runs internally at 0.30) does not
    catch the nested-box case: when a small box is fully inside a large one,
    IoU = area(small) / area(large) can sit well below the NMS threshold.
    Phone-replay frames routinely produce such pairs (face inside bezel,
    low-scale anchor inside high-scale anchor, on-screen reflection inside
    primary face).

    Adds one extra criterion on top of standard IoU:
        IoMin = intersection / min(area_A, area_B)
    which is ~1.0 for any nested pair regardless of the size ratio.

    Args:
        faces:  numpy array (N,15) from cv2.FaceDetectorYN.detect, or None.
                Columns: [x, y, w, h, 5x(lx,ly), score]. Score is the last col.
        iou_th: redundant safety net vs YuNet's internal NMS (kept loose)
        iomin_th: containment threshold; lower = more aggressive nest removal

    Returns:
        Same dtype/shape contract as `faces` (ndarray or None), with the
        lower-scoring partner of every overlapping/nested pair removed.
    """
    if faces is None or len(faces) == 0:
        return faces

    scores = faces[:, -1] if faces.shape[1] >= 15 else np.ones(len(faces))
    order = np.argsort(-scores)  # highest confidence first
    kept = []
    for idx in order:
        f = faces[idx]
        bx, by, bw, bh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
        b_area = max(1.0, bw * bh)
        suppressed = False
        for kf in kept:
            kx, ky, kw, kh = float(kf[0]), float(kf[1]), float(kf[2]), float(kf[3])
            k_area = max(1.0, kw * kh)
            ix1 = max(bx, kx); iy1 = max(by, ky)
            ix2 = min(bx + bw, kx + kw); iy2 = min(by + bh, ky + kh)
            iw = max(0.0, ix2 - ix1); ih = max(0.0, iy2 - iy1)
            inter = iw * ih
            iou = inter / (b_area + k_area - inter + 1e-6)
            iomin = inter / min(b_area, k_area)
            if iou > iou_th or iomin > iomin_th:
                suppressed = True
                break
        if not suppressed:
            kept.append(f)
    return np.array(kept) if kept else None


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

        # Detector runs at 640x480 to recover landmark / bbox spatial fidelity
        # for distant and multi-face scenes. Downstream (alignment, embedding,
        # liveness optical flow) operates on cropped face regions of fixed
        # 112x112 size, so embedding compute does NOT scale with capture
        # resolution. The only added per-frame cost is YuNet itself + one
        # full-frame BGR->Gray conversion, both Pi4-real-time-feasible.
        self.yunet = cv2.FaceDetectorYN.create(model_path1, "", (640, 480), 0.8, 0.3, 5000)
        
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

        # Per-frame, per-track diagnostic log. Decoupled from the attendance
        # log so analyze_results.py and existing schemas keep working.
        diag_exists = os.path.isfile(diag_path) and os.path.getsize(diag_path) > 0
        self.diag_file = open(diag_path, 'a', newline='')
        self.diag_writer = csv.writer(self.diag_file)
        if not diag_exists:
            self.diag_writer.writerow([
                "timestamp", "frame_w", "frame_h", "track_id",
                "lbl", "live_conf", "reason", "decision",
                "mode", "distance", "brightness",
                "avg_mag", "avg_ang_var", "avg_mag_var", "avg_area_var", "rigid_ratio",
                "m_score", "g_score",
                "identity", "sim", "th_high", "th_mid",
                "latency_ms",
            ])

    def extract_embedding(self, face_img):
        input_tensor = (np.float32(face_img) - 127.5) / 128.0
        input_tensor = np.expand_dims(input_tensor, axis=0)
        self.interpreter.set_tensor(self.in_idx, input_tensor)
        self.interpreter.invoke()
        emb = self.interpreter.get_tensor(self.out_idx)[0]
        return emb / np.linalg.norm(emb)

    def _draw_overlay(self, frame, x, y, fw, fh, track_id, dbg):
        """Draw the per-track debug overlay. Always called, regardless of
        which gate the pipeline exited on, via the try/finally in run()."""
       
        lbl = dbg['lbl']

        if dbg['decision'] == 'NO_MATCH':
            return

        # NOTE: previously had `if fw * fh < 2500: return` here. That hid every
        # validated face below 50x50 from the overlay (distant attendees).
        # Removed: is_valid_face already enforces the >=36x36 floor before any
        # detection reaches the overlay path, so this filter was redundant
        # AND was masking exactly the multi-face / far-face scenarios we're
        # trying to debug. The pipeline-level processing was never affected.


        # Color is derived from the pipeline DECISION (user-facing trust state),
        # not from the liveness label. This prevents internal debug states such
        # as UNCERTAIN/ANALYZING/BUFFERING from producing misleading yellow/orange
        # boxes that appear accepted when no recognition decision has been made.
        decision = dbg['decision']
        if decision in ('MATCHED', 'OFFLOAD_TO_CLOUD'):
            color = (0, 255, 0)        # green  — identity confirmed or escalated
        elif decision in ('REJECTED_LIVENESS', 'OUT_OF_RANGE') or lbl == 'SPOOF':
            color = (0, 0, 255)        # red    — active rejection
        else:
            # BUFFERING, BELOW_THRESHOLD, UNCERTAIN, ANALYZING, NONE, NA
            color = (180, 180, 180)    # grey   — neutral / insufficient data

        info_lines = [
            f"ID:{track_id} {dbg['mode']} d:{dbg['distance']:.2f}m",
            f"Live:{lbl} ({dbg['live_conf']:.2f})  br:{dbg['brightness']:.0f}",
            f"sim:{dbg['sim']:.2f}/th:{dbg['th_high']:.2f}  {dbg['identity']}",
            f"mag:{dbg['avg_mag']:.2f}  angV:{dbg['avg_ang_var']:.3f}  magV:{dbg['avg_mag_var']:.2f}",
            f"rigid:{dbg['rigid_ratio']:.2f}  areaV:{dbg['avg_area_var']:.0f}",
            f"D:{decision}",
            f"R:{dbg['rsn']}",
        ]
        cv2.rectangle(frame, (x, y), (x + fw, y + fh), color, 2)
        draw_debug_info(frame, x + fw + 8, max(20, y), info_lines, color)

    def _write_diag(self, loop_start, frame_w, frame_h, track_id, dbg):
        """Append one diagnostic row per (frame, track), regardless of decision."""
        latency_ms = (time.time() - loop_start) * 1000
        self.diag_writer.writerow([
            round(time.time(), 3), frame_w, frame_h, track_id,
            dbg['lbl'], round(dbg['live_conf'], 3), dbg['rsn'], dbg['decision'],
            dbg['mode'], round(dbg['distance'], 3), round(dbg['brightness'], 1),
            round(dbg['avg_mag'], 3), round(dbg['avg_ang_var'], 4),
            round(dbg['avg_mag_var'], 3), round(dbg['avg_area_var'], 1),
            round(dbg['rigid_ratio'], 3),
            round(dbg['m_score'], 3), round(dbg['g_score'], 3),
            dbg['identity'], round(dbg['sim'], 3),
            round(dbg['th_high'], 3), round(dbg['th_mid'], 3),
            round(latency_ms, 1),
        ])

    def run(self):
        cap = cv2.VideoCapture(0)
        if settings.SIMULATE_PI:
            # 640x480 capture restores spatial fidelity for far-face landmarks
            # without exploding embedding compute (embeddings remain on the
            # fixed 112x112 aligned crop). Pi realism preserved: the heavy
            # work (YuNet + cvtColor) scales 4x but stays in real-time budget.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            
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

            # 3b. Pre-tracker suppression of nested / duplicate detections that
            # YuNet's internal IoU-NMS cannot remove by construction. See
            # suppress_overlapping() docstring for the IoMin rationale. This is
            # the single intervention that stabilises tracker IDs against phone
            # replay artifacts; everything downstream (validation, tracker,
            # liveness, diagnostics) is unchanged.
            raw_count = 0 if faces is None else len(faces)
            faces = suppress_overlapping(faces)
            kept_count = 0 if faces is None else len(faces)

            # 4. Print raw debug data to console
            print(f"[DEBUG] Frame: {w}x{h}, Channels: {frame.shape[2] if len(frame.shape)>2 else 1}")
            print(f"[DEBUG] Raw faces output type: {type(faces)}")
            print(f"[DEBUG] Faces found: {kept_count} (raw {raw_count}, suppressed {raw_count - kept_count})")

            rects = []
            valid_faces = [] # 🟢 ADD THIS: Keep track of the full face data that passed validation

            if faces is not None:
                for f in faces:
                    rx, ry, rw, rh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
                    
                    # --- FACE VALIDATION GATE ---
                    crop = frame[max(0, ry):ry+rh, max(0, rx):rx+rw]
                    raw_landmarks = [(int(f[4+2*j]), int(f[4+2*j+1])) for j in range(5)]
                    
                    if not is_valid_face(crop, raw_landmarks, (rx, ry, rw, rh), w, h):
                        #cv2.rectangle(frame, (rx, ry), (rx+rw, ry+rh), (100, 100, 100), 1)
                        continue 
                    # ---------------------------------
                    
                    #cv2.rectangle(frame, (rx, ry), (rx+rw, ry+rh), (255, 0, 0), 1)
                    rects.append((rx, ry, rw, rh))
                    valid_faces.append(f) # 🟢 ADD THIS: Store the valid raw face data
                    
            objects = self.tracker.update(rects)

            for track_id, (centroid, box) in objects.items():
                x, y, fw, fh = box

                # Per-track debug snapshot. Initialised before any gate so that
                # the finally-block can always render an overlay and emit a
                # diagnostic row even if the pipeline short-circuits.
                dbg = {
                    'lbl': 'NA', 'live_conf': 0.0, 'rsn': 'init',
                    'mode': 'NA', 'distance': 0.0, 'brightness': 0.0,
                    'm_score': 0.0, 'g_score': 0.0,
                    'avg_mag': 0.0, 'avg_ang_var': 0.0, 'avg_mag_var': 0.0,
                    'avg_area_var': 0.0, 'rigid_ratio': 0.0,
                    'sim': 0.0, 'identity': 'NA',
                    'th_high': 0.0, 'th_mid': 0.0,
                    'decision': 'NONE',
                }

                try:
                    # 🟢 MODIFY THIS: Pass valid_faces instead of raw faces
                    matched_face = find_best_face_match(box, valid_faces, iou_threshold=0.3)

                    if matched_face is None:
                        self.embedding_buffers.pop(track_id, None)
                        self.liveness.history.pop(track_id, None)
                        self.liveness.last_signals.pop(track_id, None)
                        self.liveness.real_streak.pop(track_id, None)
                        self.liveness.planar_streak.pop(track_id, None)
                        dbg['rsn'] = 'No detection match'
                        dbg['decision'] = 'NO_MATCH'
                        continue

                    # Extract landmarks safely
                    landmarks = [(int(matched_face[4+2*j]), int(matched_face[4+2*j+1])) for j in range(5)]

                    # ... [The rest of your pipeline (Pose, Liveness, Embeddings, Match) remains EXACTLY the same] ...
                    mode = self.pose_est.estimate_mode(track_id, landmarks)
                    dbg['mode'] = mode

                    distance = settings.K_FOCAL / (np.sqrt(fw * fh) + 1e-5)
                    dbg['distance'] = float(distance)
                    if not (settings.MIN_DISTANCE < distance < settings.MAX_DISTANCE):
                        dbg['rsn'] = f'Distance OOR ({distance:.2f}m)'
                        dbg['decision'] = 'OUT_OF_RANGE'
                        continue

                    # FIX 4,5,6,7: Deep Temporal Liveness
                    lbl, conf, rsn, m_score, g_score = self.liveness.assess_frame(
                        track_id, mode, prev_gray, frame, box, landmarks)
                    dbg['lbl'] = lbl
                    dbg['live_conf'] = float(conf)
                    dbg['rsn'] = rsn
                    dbg['m_score'] = float(m_score)
                    dbg['g_score'] = float(g_score)

                    # Pull raw signals straight from the liveness engine for
                    # threshold calibration and the on-screen overlay.
                    sig = self.liveness.last_signals.get(track_id, {})
                    dbg['avg_mag'] = float(sig.get('avg_mag', 0.0))
                    dbg['avg_ang_var'] = float(sig.get('avg_angle_var', 0.0))
                    dbg['avg_mag_var'] = float(sig.get('avg_mag_var', 0.0))
                    dbg['avg_area_var'] = float(sig.get('avg_area_var', 0.0))
                    dbg['rigid_ratio'] = float(sig.get('rigid_ratio', 0.0))

                    # Only a confirmed SPOOF invalidates the embedding buffer.
                    # UNCERTAIN / ANALYZING are transient "we don't know" states
                    # produced by the soft rigid+planar fusion patches — wiping
                    # the buffer on them resets the 8-frame embedding window
                    # every time the liveness signal flickers, which is why
                    # MATCHED was unreachable.
                    if lbl == "SPOOF":
                        self.embedding_buffers.pop(track_id, None)
                        dbg['decision'] = 'REJECTED_LIVENESS'
                        continue
                    if lbl != "REAL":
                        # ANALYZING / UNCERTAIN: hold the buffer, don't append,
                        # don't wipe. Decision label routes to the grey/neutral
                        # overlay color (per the user-facing color mapping).
                        dbg['decision'] = lbl
                        continue

                    if track_id not in self.embedding_buffers:
                        self.embedding_buffers[track_id] = deque(maxlen=settings.LIVENESS_WINDOW)

                    # FIX 1: 5-Point Alignment
                    bgr_crop = frame[max(0,y):y+fh, max(0,x):x+fw]
                    if bgr_crop.size > 0:
                        local_landmarks = [(lx - x, ly - y) for lx, ly in landmarks]
                        aligned_face = align_face(bgr_crop, local_landmarks)
                        emb = self.extract_embedding(aligned_face)
                        self.embedding_buffers[track_id].append(emb)

                    if len(self.embedding_buffers[track_id]) < settings.LIVENESS_WINDOW:
                        dbg['decision'] = 'BUFFERING'
                        try:
                            dbg['brightness'] = float(np.mean(curr_gray[y:y+fh, x:x+fw]))
                        except Exception:
                            pass
                        continue

                    mean_emb = np.mean(self.embedding_buffers[track_id], axis=0)
                    mean_emb = mean_emb / np.linalg.norm(mean_emb)

                    # FIX 9: Pose-Aware Matching
                    identity, sim = self.controller.pose_aware_match(mean_emb, mode)
                    dbg['identity'] = identity
                    dbg['sim'] = float(sim)

                    # FIX 8: Adaptive Thresholds
                    brightness = np.mean(curr_gray[y:y+fh, x:x+fw])
                    dbg['brightness'] = float(brightness)
                    th_high, th_mid = self.controller.get_adaptive_threshold(brightness, distance, mode=="OVERHEAD")
                    dbg['th_high'] = float(th_high)
                    dbg['th_mid'] = float(th_mid)

                    if sim >= th_high:
                        dbg['decision'] = 'MATCHED'
                        if time.time() - self.cooldowns.get(identity, 0) > 300:
                            self.cooldowns[identity] = time.time()
                            print(f"ATTENDANCE MARKED: {identity}")
                    elif sim >= th_mid:
                        dbg['decision'] = 'OFFLOAD_TO_CLOUD'
                        print("OFFLOADING TO ARCFACE SERVER...")
                    else:
                        dbg['decision'] = 'BELOW_THRESHOLD'

                    # FIX 11: Comprehensive Logging
                    total_latency = (time.time() - loop_start) * 1000
                    self.csv_writer.writerow([
                        identity, round(sim, 3), time.time(), round(total_latency, 1),
                        lbl, rsn, round(distance, 2), round(brightness, 1),
                        round(m_score, 2), round(g_score, 2), mode, track_id
                    ])

                finally:
                    # Overlay + diagnostic row fire on EVERY path: NO_MATCH,
                    # OUT_OF_RANGE, REJECTED_LIVENESS, BUFFERING, MATCHED,
                    # OFFLOAD, BELOW_THRESHOLD. Required for spoof-rejection
                    # consistency and threshold calibration analyses.
                    self._draw_overlay(frame, x, y, fw, fh, track_id, dbg)
                    self._write_diag(loop_start, w, h, track_id, dbg)
                
            prev_gray = curr_gray.copy()
            cv2.namedWindow("Hybrid Edge Pipeline", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("Hybrid Edge Pipeline", 720, 540)
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
        self.diag_file.close()

if __name__ == "__main__":
    node = FinalHybridEdge()
    node.run()

# FIX-1: Deleted the trailing dead code block containing undefined references and execution outside the class scope