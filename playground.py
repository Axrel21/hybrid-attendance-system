# edge/liveness.py
import cv2
import numpy as np
from collections import deque
from config import settings

def analyze_motion(prev_gray, curr_gray, landmarks, threshold_angle=0.15, threshold_mag=1.5):
    """
    Computes optical flow on facial landmarks to detect rigid vs non-rigid motion.
    """
    pts = np.array(landmarks, dtype=np.float32).reshape(-1, 1, 2)
    
    next_pts, status, err = cv2.calcOpticalFlowPyrLK(
        prev_gray, curr_gray, pts, None,
        winSize=(15, 15), 
        maxLevel=2,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03)
    )
    
    # ✅ FIX 1: CRASH RISK PREVENTED
    # Safely handle cases where OpenCV returns None due to lost tracking or edge cases
    if next_pts is None or status is None:
        return 0.0, 0.0, 0.0, False
        
    valid_mask = (status == 1).flatten()
    good_new = next_pts[valid_mask].reshape(-1, 2)
    good_old = pts[valid_mask].reshape(-1, 2)
    
    if len(good_new) < 3:
        return 0.0, 0.0, 0.0, False
        
    flow = good_new - good_old
    dx = flow[:, 0]
    dy = flow[:, 1]
    
    magnitudes = np.linalg.norm(flow, axis=1)
    
    # ✅ FIX 3: NUMERICAL STABILITY
    # Added 1e-6 to prevent divide-by-zero or instability when dx/dy are exactly 0.0
    angles = np.arctan2(dy + 1e-6, dx + 1e-6)               
    
    magnitude_mean = float(np.mean(magnitudes))
    magnitude_variance = float(np.var(magnitudes))
    angle_variance = float(np.var(angles))
    
    # ✅ FIX 2: HARD-CODED THRESHOLD REMOVED
    # Uses configurable setting so it can be adapted based on camera distance/resolution
    is_moving = magnitude_mean > settings.MOTION_MIN_THRESHOLD 
    is_rigid = bool(is_moving and angle_variance < threshold_angle and magnitude_variance < threshold_mag)
    
    return magnitude_mean, angle_variance, magnitude_variance, is_rigid

class LivenessEngine:
    def __init__(self):
        self.history = {}

    def initialize_track(self, track_id):
        if track_id not in self.history:
            self.history[track_id] = deque(maxlen=settings.LIVENESS_WINDOW)

    def get_texture_metrics(self, bgr_crop):
        """ Calculates Skin Ratio and basic Image Entropy """
        # Skin Ratio
        hsv = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 20, 70), (20, 255, 255))
        skin_ratio = np.sum(mask > 0) / (mask.size + 1e-6)
        
        # Fast Entropy (using Histogram)
        gray = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        hist_norm = hist.ravel() / hist.sum()
        entropy = -np.sum(hist_norm * np.log2(hist_norm + 1e-7))
        
        return skin_ratio, entropy, gray

    def assess_frame(self, track_id, current_mode, prev_gray, frame, curr_box, landmarks):
        self.initialize_track(track_id)
        x, y, w, h = curr_box
        bgr_crop = frame[y:y+h, x:x+w]
        
        stats = {} # FIX-5: Declared stats dictionary early to prevent NameError
        
        if prev_gray is not None:
            motion_score, ang_var, mag_var, rigid_flag = analyze_motion(
                prev_gray, curr_gray, landmarks, 
                threshold_angle=settings.RIGID_ANGLE_VAR_TH, 
                threshold_mag=settings.RIGID_MAG_VAR_TH
            )
    
            stats['mag'] = motion_score
            stats['angle_var'] = ang_var
            stats['mag_var'] = mag_var      # FIX-6: Populated from correctly executed analyze_motion
            stats['is_rigid'] = rigid_flag  # FIX-6: Populated from correctly executed analyze_motion
        else:
            stats['mag'], stats['angle_var'], stats['mag_var'], stats['is_rigid'] = 0.0, 0.0, 0.0, False
        
        if bgr_crop.size == 0: return "UNKNOWN", 0.0, "Invalid Crop", 0, 0
        
        # FIX-7: Unpack texture metrics to crop_gray so it doesn't overwrite/shadow the full-frame curr_gray needed for flow tracking
        skin_ratio, entropy, crop_gray = self.get_texture_metrics(bgr_crop) 
        
        stats['skin'] = skin_ratio
        stats['entropy'] = entropy
        stats['blur'] = cv2.Laplacian(crop_gray, cv2.CV_64F).var() # FIX-7: Applies to isolated face crop grayscale
        stats['brightness'] = np.mean(crop_gray)                   # FIX-7: Applies to isolated face crop grayscale
        stats['area'] = w * h
        stats['centroid'] = (x + w/2.0, y + h/2.0)

        # FIX-6: Deleted redundant optical flow block that overwrote variables with incorrect attributes

        self.history[track_id].append(stats)
        return self._temporal_vote(track_id, current_mode)

    # FIX-4: Correctly indented _temporal_vote to be an instance method of LivenessEngine
    def _temporal_vote(self, track_id, mode):
        hist = self.history[track_id]
        if len(hist) < settings.LIVENESS_WINDOW:
            return "ANALYZING", 0.5, "Buffering frames", 0.0, 0.0

        # Extract Time-Series Data
        areas = [s['area'] for s in hist]
        mags = [s['mag'] for s in hist]
        angle_vars = [s['angle_var'] for s in hist]
        mag_vars = [s['mag_var'] for s in hist]
        skins = [s['skin'] for s in hist]
        blurs = [s['blur'] for s in hist]
        brightnesses = [s['brightness'] for s in hist]

        # Calculate averages and variances over the window
        avg_mag = np.mean(mags)
        avg_angle_var = np.mean(angle_vars)
        avg_mag_var = np.mean(mag_vars)
        area_var = np.var(areas)
        avg_skin = np.mean(skins)
        avg_blur = np.mean(blurs)
        avg_bright = np.mean(brightnesses)

        # =========================================================
        # 🛡️ HARD REJECTION: THE ANTI-SPOOFING GATE
        # =========================================================
        
        is_moving = avg_mag > settings.MOTION_MIN_THRESHOLD
        
        # 1. The Planar Motion Trap (Defeats sliding a phone or paper)
        # If it moves, but all points move identically, it is a 2D surface.
        is_planar_motion = is_moving and (avg_angle_var < settings.RIGID_ANGLE_VAR_TH) and (avg_mag_var < settings.RIGID_MAG_VAR_TH)
        
        # 2. The Static Depth Trap (Defeats holding a phone/paper perfectly still)
        # Real humans cannot hold their head perfectly still at the millimeter level.
        is_static_depth = area_var < settings.MIN_AREA_VAR_TH

        # 3. The Screen Glare Trap
        is_screen_glare = avg_bright > settings.MAX_BRIGHTNESS_TH and avg_blur > settings.SCREEN_LAPLACIAN_TH

        # --- Evaluate Traps ---
        if is_planar_motion:
            return "SPOOF", 0.1, "Planar Motion Detected (Phone/Paper)", avg_mag, area_var
            
        if is_static_depth and not is_moving:
            return "SPOOF", 0.1, "Rigid Depth Detected (Static Photo)", avg_mag, area_var
            
        if is_screen_glare:
            return "SPOOF", 0.2, "Artificial Screen Glare", avg_mag, area_var

        # =========================================================
        # 🟢 LIVENESS SCORING (For faces that pass the hard blocks)
        # =========================================================
        
        # Adaptive Skin Normalization (Forgiving in low light)
        skin_target = 0.30 if avg_bright > 60 else 0.15 
        norm_skin = min(avg_skin / skin_target, 1.0)

        # Motion Normalization
        norm_motion = min(avg_mag / 1.5, 1.0)
        norm_angle_var = min(avg_angle_var / 0.3, 1.0)
        norm_area_var = min(area_var / 100.0, 1.0)

        # Composite Scores
        motion_score = (norm_motion * 0.4) + (norm_angle_var * 0.6)
        geometry_score = norm_area_var
        texture_score = norm_skin

        # Mode-Adaptive Weighting
        if mode == "OVERHEAD":
            # Rely more on 3D geometry changes, less on facial texture
            w_motion, w_geom, w_text = 0.3, 0.5, 0.2
        else: # FRONTAL
            w_motion, w_geom, w_text = 0.4, 0.2, 0.4

        final_score = (w_motion * motion_score) + (w_geom * geometry_score) + (w_text * texture_score)

        if final_score >= 0.70:
            return "REAL", final_score, "High Confidence", motion_score, geometry_score
        elif final_score >= 0.40:
            return "UNCERTAIN", final_score, "Ambiguous Signals", motion_score, geometry_score
        else:
            return "SPOOF", final_score, "Low Liveness Score", motion_score, geometry_score