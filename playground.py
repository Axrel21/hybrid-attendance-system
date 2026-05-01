import cv2
import numpy as np

def is_valid_face(face_crop, landmarks, bbox, frame_w, frame_h):
    """
    Strict heuristic filter to reject false positive face detections 
    (fingers, cartoons, screens, bad crops).
    """
    x, y, w, h = bbox
    
    # 1. Size Check
    if w < 40 or h < 40 or w > frame_w * 0.8 or h > frame_h * 0.8:
        return False

    # 2. Strict Aspect Ratio Check
    # Real frontal/tilted faces are roughly square/slightly rectangular. 
    # Fingers are extreme strips (e.g., 0.3 or 2.0).
    aspect_ratio = float(w) / float(h)
    if aspect_ratio < 0.65 or aspect_ratio > 1.25: 
        return False

    # 3. Valid Crop Verification
    if face_crop is None or face_crop.size == 0 or face_crop.shape[0] == 0 or face_crop.shape[1] == 0:
        return False

    # 4. Strict Skin Color Check (HSV)
    hsv = cv2.cvtColor(face_crop, cv2.COLOR_BGR2HSV)
    
    # TIGHTENED: 
    # - Saturation [30-200] rejects grayscale/smooth cartoons (low S) and neon graphics (high S).
    # - Value [60-240] rejects pure black shadows and artificial screen glare (V>240).
    lower_skin = np.array([0, 30, 60], dtype=np.uint8)
    upper_skin = np.array([25, 200, 240], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_skin, upper_skin)
    skin_ratio = np.sum(mask > 0) / (mask.size + 1e-6)
    
    # TIGHTENED: A real face crop from YuNet is mostly face.
    if skin_ratio < 0.35: 
        return False

    # 5. Texture/Entropy Check (Laplacian Variance)
    gray_crop = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    blur_score = cv2.Laplacian(gray_crop, cv2.CV_64F).var()
    
    # TIGHTENED: 
    # < 30.0: Rejects cartoons/printed paper (unnatural smoothness, no pores/micro-shadows).
    # > 3000.0: Rejects phone screens held close (high-frequency Moiré patterns / pixel grids).
    if blur_score < 30.0 or blur_score > 3000.0: 
        return False

    # 6. Strict Landmark Geometry Check
    # YuNet landmarks: [left_eye, right_eye, nose, left_mouth, right_mouth]
    lx, ly = landmarks[0]
    rx, ry = landmarks[1]
    nx, ny = landmarks[2]
    lmx, lmy = landmarks[3]
    rmx, rmy = landmarks[4]
    
    # A. Basic orientation (Eyes must be above mouth)
    if ly > lmy and ry > rmy:
        return False

    # B. Inter-Ocular Distance (IOD)
    # The distance between eyes must make geometric sense relative to the bounding box width.
    eye_dx = abs(rx - lx)
    eye_dy = abs(ry - ly)
    iod = np.sqrt(eye_dx**2 + eye_dy**2)
    
    # If eyes are too close (fingers) or too far apart (glitched crop) -> Reject
    if iod < (w * 0.20) or iod > (w * 0.60):
        return False

    # C. Horizontal Alignment Check
    # If YuNet hallucinates a face on a vertical finger, the "eyes" will be stacked vertically.
    # Real human necks cannot snap to an angle where eye_dy is > 35% of the face width.
    if eye_dy > (w * 0.35):
        return False

    return True