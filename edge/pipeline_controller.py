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

        # TEMP_REC_DEBUG: trace every recognition attempt. Remove after the
        # alignment/buffer fixes are confirmed in the diagnostic CSV.
        q_norm = float(np.linalg.norm(mean_embedding))
        per_user_summary = []

        for name, vectors in self.db.items():
            # Adaptive routing based on POSE
            if mode == "FRONTAL":
                pool = vectors.get("frontal", [])
                pool_label = "F"
            else:
                pool = vectors.get("angled", [])
                pool_label = "A"
                if len(pool) == 0:
                    pool = vectors.get("frontal", [])  # Fallback
                    pool_label = "F(fb)"

            per_user_max = -1.0
            db_norms = []
            for v in pool:
                v = np.array(v, dtype=np.float32)
                v_raw_norm = float(np.linalg.norm(v))
                db_norms.append(v_raw_norm)
                v = v / (v_raw_norm + 1e-6)
                sim = float(np.dot(mean_embedding, v))
                if sim > per_user_max:
                    per_user_max = sim
                if sim > max_sim:
                    max_sim, best_match = sim, name

            # TEMP_REC_DEBUG: per-identity candidate count + raw-norm sanity + max sim
            if pool:
                per_user_summary.append(
                    f"{name}[{pool_label}:{len(pool)}|n̄={np.mean(db_norms):.3f}|max={per_user_max:.3f}]"
                )
            else:
                per_user_summary.append(f"{name}[{pool_label}:0]")

        # TEMP_REC_DEBUG: one compact line per call
        print(
            f"[REC] mode={mode} q|shape={tuple(mean_embedding.shape)}|‖q‖={q_norm:.3f} → "
            f"{' '.join(per_user_summary) or '<empty_db>'} → BEST={best_match}@{max_sim:.3f}"
        )

        return best_match, max_sim

    def get_identity_status(self, identity):
        """ Returns status of identity in the database. """
        return self.db.get(identity, {}).get("status", "UNKNOWN")