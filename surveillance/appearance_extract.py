"""Trigger-based OSNet x0.25 appearance embedding — experimental continuity only.

Embeddings are extracted ONLY when explicitly invoked by presence_sync triggers.
Never run per-frame or continuously.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import cv2
import numpy as np

log = logging.getLogger("surveillance.appearance_extract")

_extractor: Any | None = None
_extractor_failed = False
OSNET_DIM = 512


def appearance_continuity_enabled() -> bool:
    raw = os.environ.get("SURVEILLANCE_APPEARANCE_CONTINUITY", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _get_extractor() -> Any | None:
    global _extractor, _extractor_failed
    if _extractor_failed:
        return None
    if _extractor is not None:
        return _extractor
    try:
        from torchreid.utils import FeatureExtractor

        _extractor = FeatureExtractor(
            model_name="osnet_x0_25",
            device="cpu",
        )
        log.info("Appearance continuity: osnet_x0_25 loaded (CPU, trigger-only)")
        return _extractor
    except Exception as exc:  # noqa: BLE001
        _extractor_failed = True
        log.warning(
            "Appearance continuity disabled — could not load osnet_x0_25 (%s). "
            "Install optional deps: pip install -r surveillance/requirements-appearance.txt",
            exc,
        )
        return None


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-12:
        return vec
    return vec / norm


def extract_track_embedding(
    frame: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> list[float] | None:
    """One-shot L2-normalized OSNet embedding for a person crop. Never raises."""
    if not appearance_continuity_enabled():
        return None

    extractor = _get_extractor()
    if extractor is None or frame is None or frame.size == 0:
        return None

    x1, y1, x2, y2 = bbox
    height, width = frame.shape[:2]
    x1 = max(0, min(width - 1, int(x1)))
    y1 = max(0, min(height - 1, int(y1)))
    x2 = max(x1 + 1, min(width, int(x2)))
    y2 = max(y1 + 1, min(height, int(y2)))
    if (x2 - x1) < 20 or (y2 - y1) < 40:
        return None

    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    try:
        from PIL import Image

        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb)
        features = extractor([pil_image])
        vec = features.detach().cpu().numpy().reshape(-1).astype(np.float32)
        if vec.size == 0:
            return None
        vec = _normalize(vec)
        return [round(float(v), 6) for v in vec.tolist()]
    except Exception as exc:  # noqa: BLE001
        log.debug("Appearance embedding extraction failed: %s", exc)
        return None
