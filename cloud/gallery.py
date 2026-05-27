"""
FaceGallery — Enrolled identity store

Responsibilities:
  - Store and index enrolled identity embeddings
  - Perform nearest-neighbour search for verification
  - Persist/load gallery from disk (numpy format)

Design:
  Kept intentionally simple — a matrix of L2-normalised embeddings
  with a corresponding list of identity labels.

  For research scale (tens to low hundreds of identities), a brute-force
  cosine search is faster than FAISS due to overhead. FAISS can be
  introduced later without changing the interface.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

log = logging.getLogger("face_gallery")

# Per-identity, we store the MEAN of all enrolled embeddings for that person.
# This improves robustness vs storing only one sample.


class FaceGallery:
    def __init__(self, verifier=None):
        """
        Args:
            verifier: ArcFaceVerifier instance (used only for extract_embedding
                      if enrolling from raw images; not needed for embedding-only flow)
        """
        self._verifier = verifier

        # Parallel structures — maintained in sync
        self._identities: list[str] = []          # identity label per entry
        self._embeddings: list[np.ndarray] = []   # (D,) per entry
        self._sources: list[str] = []             # enrollment source tag
        self._matrix: Optional[np.ndarray] = None  # (N, D) — rebuilt on demand

        self._matrix_dirty = True

    # Expected embedding dimensionality for this gallery.
    # Set to 512 because this gallery is enrolled via ArcFace (InsightFace).
    # MobileFaceNet (128-d) embeddings from the edge are NEVER stored here.
    EXPECTED_DIM: int = 512

    # ── Enrollment ────────────────────────────────────────────────────────────

    def enroll(self, identity: str, embedding: np.ndarray, source: str = "manual"):
        """
        Add or update a single enrollment for an identity.

        Accepts ONLY 512-d ArcFace embeddings (extracted server-side via InsightFace).
        Raises ValueError if a 128-d MobileFaceNet embedding is mistakenly passed.

        If the identity already exists, the existing embedding is averaged
        with the new one (mean-face approach).
        """
        embedding = embedding.astype(np.float32)

        # ── Hard dimensionality guard ──────────────────────────────────────────
        if embedding.shape[0] != self.EXPECTED_DIM:
            raise ValueError(
                f"FaceGallery.enroll: expected {self.EXPECTED_DIM}-d ArcFace embedding, "
                f"got {embedding.shape[0]}-d. "
                f"Do NOT pass MobileFaceNet (128-d) embeddings to this gallery. "
                f"ArcFace extraction must be performed server-side before enrollment."
            )

        embedding = embedding / (np.linalg.norm(embedding) + 1e-8)

        if identity in self._identities:
            idx = self._identities.index(identity)
            # Incremental mean: new_mean = (old_mean + new_emb) / 2, re-normalised
            combined = self._embeddings[idx] + embedding
            combined = combined / (np.linalg.norm(combined) + 1e-8)
            self._embeddings[idx] = combined
            log.debug(f"Updated existing enrollment for '{identity}'")
        else:
            self._identities.append(identity)
            self._embeddings.append(embedding)
            self._sources.append(source)

        self._matrix_dirty = True

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: np.ndarray) -> Tuple[Optional[str], float]:
        """
        Find the closest identity in the gallery using cosine similarity.

        Args:
            query: (512,) L2-normalised ArcFace embedding

        Returns:
            (identity_label, similarity_score)
            Returns (None, 0.0) if gallery is empty.

        Raises:
            ValueError if query is not 512-d (guards against MobileFaceNet leakage).
        """
        if query.shape[0] != self.EXPECTED_DIM:
            raise ValueError(
                f"FaceGallery.search: expected {self.EXPECTED_DIM}-d query embedding, "
                f"got {query.shape[0]}-d. "
                f"Ensure ArcFace extraction happens server-side before calling search."
            )
        if len(self._identities) == 0:
            log.warning("Gallery is empty — cannot verify")
            return None, 0.0

        matrix = self._get_matrix()
        scores = matrix @ (query / (np.linalg.norm(query) + 1e-8))
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])

        return self._identities[best_idx], best_score

    def top_k_search(self, query: np.ndarray, k: int = 3) -> list[Tuple[str, float]]:
        """
        Return top-k closest identities with scores.
        Useful for research analysis of score distributions.
        """
        if len(self._identities) == 0:
            return []

        matrix = self._get_matrix()
        scores = matrix @ (query / (np.linalg.norm(query) + 1e-8))
        top_idx = np.argsort(scores)[::-1][:k]

        return [(self._identities[i], float(scores[i])) for i in top_idx]

    # ── Persistence ───────────────────────────────────────────────────────────

    def load_from_disk(self, gallery_dir: str):
        """
        Load gallery from a directory of .npy files.

        Expected structure:
          gallery/
            student_001.npy   ← (D,) embedding
            student_002.npy
            ...

        Each file = one enrolled identity.
        Filename stem = identity label.
        """
        gallery_path = Path(gallery_dir)
        if not gallery_path.exists():
            log.warning(f"Gallery directory '{gallery_dir}' not found — starting with empty gallery")
            return

        loaded = 0
        for npy_file in sorted(gallery_path.glob("*.npy")):
            identity = npy_file.stem
            try:
                emb = np.load(str(npy_file)).astype(np.float32)
                self.enroll(identity, emb, source="disk")
                loaded += 1
            except Exception as e:
                log.error(f"Failed to load gallery entry '{npy_file}': {e}")

        log.info(f"Loaded {loaded} identities from '{gallery_dir}'")
        self._matrix_dirty = True

    def save_to_disk(self, gallery_dir: str):
        """Persist current gallery to .npy files."""
        gallery_path = Path(gallery_dir)
        gallery_path.mkdir(parents=True, exist_ok=True)

        for identity, emb in zip(self._identities, self._embeddings):
            out_path = gallery_path / f"{identity}.npy"
            np.save(str(out_path), emb)

        log.info(f"Saved {len(self._identities)} identities to '{gallery_dir}'")

    # ── Introspection ─────────────────────────────────────────────────────────

    def identity_list(self) -> list[str]:
        return list(self._identities)

    def __len__(self) -> int:
        return len(self._identities)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_matrix(self) -> np.ndarray:
        """Lazily build/return the (N, D) gallery matrix."""
        if self._matrix_dirty or self._matrix is None:
            if self._embeddings:
                self._matrix = np.stack(self._embeddings, axis=0).astype(np.float32)
            else:
                self._matrix = np.zeros((0, self.EXPECTED_DIM), dtype=np.float32)
            self._matrix_dirty = False
        return self._matrix