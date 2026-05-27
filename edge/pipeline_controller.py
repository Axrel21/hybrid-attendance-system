# edge/pipeline_controller.py
from config import settings
from config.logging_setup import LOG_DEBUG

import numpy as np


class PipelineController:
    def __init__(self, db):
        # Pre-convert and L2-normalise all stored embeddings once at load time
        # (P4 bottleneck fix: removes ~150 np.array() allocations + normalisations
        # per recognition call for a 30-student class with 5 embeddings each).
        # pose_aware_match() now does only dot-products against pre-normalised
        # numpy arrays — no per-call allocation, no per-call norm computation.
        self.db: dict[str, dict[str, list[np.ndarray]]] = {}
        for name, pools in db.items():
            self.db[name] = {}
            for pool_name, vectors in pools.items():
                normed: list[np.ndarray] = []
                for v in vectors:
                    arr = np.array(v, dtype=np.float32)
                    n = np.linalg.norm(arr)
                    normed.append(arr / (n + 1e-6))
                self.db[name][pool_name] = normed

        # Per-call meta from the most recent pose_aware_match() invocation.
        # Read by main._write_diag for the orientation calibration log.
        # Schema:
        #   {
        #       'pool_used': str,   # 'frontal' | 'angled' | 'frontal_fallback'
        #       'pool_size': int,   # total number of stored vectors compared
        #       'num_identities': int,
        #   }
        self.last_match_meta: dict = {}

    def get_adaptive_threshold(self, brightness, distance, is_overhead):
        """ Dynamically adjusts thresholds based on environment. """
        th_high = settings.MATCH_HIGH_BASE

        if brightness < 60:  # Low light
            th_high += 0.05  # Stricter
        if distance > 2.5:   # Far away
            th_high += 0.03
        if is_overhead:
            th_high -= 0.02  # Relax slightly for overhead distortions

        return th_high, settings.MATCH_MID_BASE

    def pose_aware_match(self, mean_embedding, mode):
        """ Selects frontal vs angled embeddings based on face mode. """
        best_match, max_sim = "UNKNOWN", -1
        # pool_used records which pool the *best-matching* identity came from.
        best_pool_used = "NA"

        q_norm = float(np.linalg.norm(mean_embedding))
        per_user_summary = []
        total_pool_size = 0

        for name, pools in self.db.items():
            # Adaptive routing based on POSE
            if mode == "FRONTAL":
                pool = pools.get("frontal", [])
                pool_label = "F"
                pool_kind = "frontal"
            else:
                pool = pools.get("angled", [])
                pool_label = "A"
                pool_kind = "angled"
                if len(pool) == 0:
                    pool = pools.get("frontal", [])  # Fallback
                    pool_label = "F(fb)"
                    pool_kind = "frontal_fallback"

            total_pool_size += len(pool)
            per_user_max = -1.0

            for v in pool:
                # v is already a normalised np.float32 array (pre-computed in __init__)
                sim = float(np.dot(mean_embedding, v))
                if sim > per_user_max:
                    per_user_max = sim
                if sim > max_sim:
                    max_sim, best_match = sim, name
                    best_pool_used = pool_kind

            if pool:
                per_user_summary.append(
                    f"{name}[{pool_label}:{len(pool)}|max={per_user_max:.3f}]"
                )
            else:
                per_user_summary.append(f"{name}[{pool_label}:0]")

        self.last_match_meta = {
            "pool_used": best_pool_used,
            "pool_size": int(total_pool_size),
            "num_identities": int(len(self.db)),
        }

        if settings.VERBOSE_DEBUG:
            LOG_DEBUG.debug(
                "[REC] mode=%s q_shape=%s q_norm=%.3f -> %s -> BEST=%s@%.3f",
                mode,
                tuple(mean_embedding.shape),
                q_norm,
                " ".join(per_user_summary) or "<empty_db>",
                best_match,
                max_sim,
            )

        return best_match, max_sim

    def get_identity_status(self, identity):
        """ Returns status of identity in the database. """
        return self.db.get(identity, {}).get("status", "UNKNOWN")
