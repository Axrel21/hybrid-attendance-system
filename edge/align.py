# edge/align.py
import cv2
import numpy as np

# Standard landmarks for 112x112 MobileFaceNet/ArcFace input
STANDARD_LANDMARKS = np.array([
    [38.2946, 51.6963], # Left eye
    [73.5318, 51.5014], # Right eye
    [56.0252, 71.7366], # Nose
    [41.5493, 92.3655], # Left mouth
    [70.7299, 92.2041]  # Right mouth
], dtype=np.float32)

def align_face(img, landmarks):
    """ Applies a 5-point similarity transform. """
    # YuNet landmarks shape: [(x,y), (x,y), (x,y), (x,y), (x,y)]
    src_pts = np.array(landmarks, dtype=np.float32)
    
    # Estimate partial affine transform (rotation, translation, scale)
    M, _ = cv2.estimateAffinePartial2D(src_pts, STANDARD_LANDMARKS, method=cv2.LMEDS)
    
    if M is None:
        return cv2.resize(img, (112, 112)) # Fallback
        
    aligned_face = cv2.warpAffine(img, M, (112, 112), borderValue=0.0)
    return aligned_face