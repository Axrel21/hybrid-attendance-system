# edge/pipeline_controller.py
from config import settings
import numpy as np


class PipelineController:
    def __init__(self, db):
        self.db = db
        # Per-call meta from the most recent pose_aware_match() invocation.
        # Read by main._write_diag for the orientation calibration log so
        # we can correlate which embedding pool (frontal vs angled vs
        # frontal-fallback) actually drove the recognition decision under
        # each pose mode. Schema:
        #   {
        #       'pool_used': str,   # 'frontal' | 'angled' | 'frontal_fallback'
        #       'pool_size': int,   # number of stored vectors compared
        #       'num_identities': int,
        #   }
        self.last_match_meta = {}

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
        # pool_used records which pool *the best-matching identity*
        # actually contributed from. This is more useful for analysis
        # than a global flag — it lets us split sim distributions per
        # (mode, pool_actually_used) without ambiguity.
        best_pool_used = "NA"

        q_norm = float(np.linalg.norm(mean_embedding))
        per_user_summary = []
        total_pool_size = 0

        for name, vectors in self.db.items():
            # Adaptive routing based on POSE
            if mode == "FRONTAL":
                pool = vectors.get("frontal", [])
                pool_label = "F"
                pool_kind = "frontal"
            else:
                pool = vectors.get("angled", [])
                pool_label = "A"
                pool_kind = "angled"
                if len(pool) == 0:
                    pool = vectors.get("frontal", [])  # Fallback
                    pool_label = "F(fb)"
                    pool_kind = "frontal_fallback"

            total_pool_size += len(pool)

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
                    best_pool_used = pool_kind

            # TEMP_REC_DEBUG: per-identity candidate count + raw-norm sanity + max sim
            # ASCII-only so the line prints cleanly on Windows cp1252 consoles.
            if pool:
                per_user_summary.append(
                    f"{name}[{pool_label}:{len(pool)}|n_avg={np.mean(db_norms):.3f}|max={per_user_max:.3f}]"
                )
            else:
                per_user_summary.append(f"{name}[{pool_label}:0]")

        self.last_match_meta = {
            "pool_used": best_pool_used,
            "pool_size": int(total_pool_size),
            "num_identities": int(len(self.db)),
        }

        if settings.VERBOSE_DEBUG:
            print(
                f"[REC] mode={mode} q|shape={tuple(mean_embedding.shape)}|q_norm={q_norm:.3f} -> "
                f"{' '.join(per_user_summary) or '<empty_db>'} -> BEST={best_match}@{max_sim:.3f}"
            )

        return best_match, max_sim

    def get_identity_status(self, identity):
        """ Returns status of identity in the database. """
        return self.db.get(identity, {}).get("status", "UNKNOWN")
