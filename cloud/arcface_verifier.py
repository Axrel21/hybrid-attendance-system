"""
ArcFace Verifier — InsightFace wrapper

Handles:
  - Model loading (arcface_r100_v1 or buffalo_l)
  - Embedding extraction from cropped face images
  - Cosine similarity computation against gallery embeddings

Design decision:
  The verifier is stateless — it only handles model inference.
  Gallery state is managed by FaceGallery separately.
  This makes unit testing and swapping models straightforward.
"""

import logging
import time
from typing import Optional

import numpy as np

log = logging.getLogger("arcface_verifier")


class ArcFaceVerifier:
    """
    Thin wrapper around InsightFace ArcFace model.

    Provides:
      - extract_embedding(face_img_bgr) -> np.ndarray (512-d, L2-normalised)
      - cosine_similarity(vec_a, vec_b) -> float
      - batch_cosine_similarity(query, gallery_matrix) -> np.ndarray

    Note on embedding dimensionality:
      InsightFace ArcFace (r100) produces 512-d embeddings.
      MobileFaceNet on edge produces 128-d embeddings.
      Gallery search uses whichever dimensionality the gallery was enrolled with.
      When edge sends 128-d MobileFaceNet embeddings, we compare directly
      against a MobileFaceNet-enrolled gallery — no cross-model projection.
      When the gallery is enrolled via ArcFace (offline), we compare 512-d.
    """

    def __init__(self, model_name: str = "buffalo_l", providers: Optional[list] = None):
        """
        Args:
            model_name: InsightFace model pack name.
                        'buffalo_l'  — accurate, ~700MB, good for server
                        'buffalo_sc' — lighter alternative
            providers:  ONNX Runtime execution providers.
                        Defaults to ['CUDAExecutionProvider', 'CPUExecutionProvider']
        """
        self.model_name = model_name
        self._app = None
        self._recognition_model = None
        self._load(providers)

    def _load(self, providers: Optional[list]):
        t0 = time.perf_counter()

        try:
            import insightface
            from insightface.app import FaceAnalysis
        except ImportError:
            raise RuntimeError(
                "insightface not installed. "
                "Run: pip install insightface onnxruntime-gpu"
            )

        if providers is None:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        log.info(f"Loading InsightFace model pack '{self.model_name}' ...")
        self._app = FaceAnalysis(
            name=self.model_name,
            allowed_modules=["recognition"],   # skip detection — edge handles that
            providers=providers,
        )
        self._app.prepare(ctx_id=0, det_size=(160, 160))

        elapsed = (time.perf_counter() - t0) * 1000
        log.info(f"InsightFace loaded in {elapsed:.1f} ms")

    def extract_embedding(self, face_img_bgr: np.ndarray) -> Optional[np.ndarray]:
        """
        Extract ArcFace embedding from a pre-cropped, pre-aligned face image.

        Args:
            face_img_bgr: BGR uint8 image, ideally 112x112 aligned face crop.

        Returns:
            512-d L2-normalised embedding, or None if extraction fails.
        """
        try:
            faces = self._app.get(face_img_bgr)
            if not faces:
                return None

            # Take highest-det-score face (should be only one for crops)
            face = max(faces, key=lambda f: f.det_score)
            emb = face.embedding
            norm = np.linalg.norm(emb)
            if norm > 1e-6:
                emb = emb / norm
            return emb.astype(np.float32)

        except Exception as e:
            log.error(f"Embedding extraction failed: {e}")
            return None

    # ── Similarity utilities ──────────────────────────────────────────────────

    @staticmethod
    def cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """
        Cosine similarity between two L2-normalised vectors.
        Returns value in [-1, 1]; higher = more similar.
        For pre-normalised vectors this is equivalent to dot product.
        """
        a = vec_a / (np.linalg.norm(vec_a) + 1e-8)
        b = vec_b / (np.linalg.norm(vec_b) + 1e-8)
        return float(np.dot(a, b))

    @staticmethod
    def batch_cosine_similarity(
        query: np.ndarray,
        gallery_matrix: np.ndarray
    ) -> np.ndarray:
        """
        Compute cosine similarity between a query vector and all gallery vectors.

        Args:
            query:          (D,) query embedding
            gallery_matrix: (N, D) matrix of gallery embeddings (L2-normalised)

        Returns:
            (N,) similarity scores
        """
        q = query / (np.linalg.norm(query) + 1e-8)
        # gallery assumed pre-normalised
        return gallery_matrix @ q