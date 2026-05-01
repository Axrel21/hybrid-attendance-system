# edge/pipeline_controller.py
from config import settings
import numpy as np

class PipelineController:
    def __init__(self, db):
        self.db = db

    def get_adaptive_threshold(self, brightness, distance, is_overhead):
        """ Dynamically adjusts thresholds based on environment. """
        th_high = settings.MATCH_HIGH_BASE
        
        if brightness < 60: # Low light
            th_high += 0.05 # Stricter
        if distance > 2.5:  # Far away
            th_high += 0.03
        if is_overhead:
            th_high -= 0.02 # Relax slightly for overhead distortions
            
        return th_high, settings.MATCH_MID_BASE

    def pose_aware_match(self, mean_embedding, mode):
        """ Selects frontal vs angled embeddings based on face mode. """
        best_match, max_sim = "UNKNOWN", -1
        
        for name, vectors in self.db.items():
            # Adaptive routing based on POSE
            if mode == "FRONTAL":
                pool = vectors.get("frontal", [])
            else:
                pool = vectors.get("angled", [])
                if len(pool) == 0: pool = vectors.get("frontal", []) # Fallback
                
            for v in pool:
                sim = np.dot(mean_embedding, v)
                if sim > max_sim:
                    max_sim, best_match = sim, name
                    
        return best_match, max_sim