# edge/orientation.py
import numpy as np
from collections import deque
from config import settings

class PoseEstimator:
    def __init__(self):
        self.mode_history = {}

    def estimate_mode(self, track_id, landmarks):
        """ Classifies face as FRONTAL, TILTED, or OVERHEAD based on geometric ratios. """
        if track_id not in self.mode_history:
            self.mode_history[track_id] = deque(maxlen=5)

        left_eye, right_eye, nose, left_mouth, right_mouth = landmarks
        
        # Inter-ocular distance vs eye-to-mouth distance
        eye_dist = np.linalg.norm(np.array(left_eye) - np.array(right_eye))
        mouth_center = ((left_mouth[0] + right_mouth[0])/2, (left_mouth[1] + right_mouth[1])/2)
        eye_center = ((left_eye[0] + right_eye[0])/2, (left_eye[1] + right_eye[1])/2)
        vertical_dist = np.linalg.norm(np.array(eye_center) - np.array(mouth_center))

        ratio = vertical_dist / (eye_dist + 1e-6)
        
        # Heuristics for mode
        if ratio < 0.6:
            current_mode = "OVERHEAD"
        elif ratio < 0.9:
            current_mode = "TILTED"
        else:
            current_mode = "FRONTAL"

        self.mode_history[track_id].append(current_mode)
        
        # Temporal Smoothing: Return most frequent mode
        return max(set(self.mode_history[track_id]), key=self.mode_history[track_id].count)