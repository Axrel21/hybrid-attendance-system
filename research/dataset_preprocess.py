"""
preprocess_dataset.py
=====================

Convert raw user images in `dataset_raw/<identity>/` into canonical, aligned,
deployment-ready 112x112 face crops in `dataset_processed/<identity>/`.

Output format: WEBP (lossy near-lossless), deterministic filenames:
    dataset_processed/<identity>/001.webp
    dataset_processed/<identity>/002.webp
    ...

Pipeline per image:
    1. Read with cv2 (any of .jpg / .jpeg / .png / .webp)
    2. Detect faces with YuNet
    3. Pick the largest face (presumed enrollment subject)
    4. Quality gate: detection score, bbox size, aspect ratio, eye distance, blur
    5. Crop to bbox and localize landmarks to crop coordinates
    6. align_face() — same 5-point similarity transform used by runtime
    7. Save the resulting 112x112 BGR as WEBP

The preprocessing is intentionally minimal: only operations that match the
runtime path in `edge/main.py` are applied. This keeps DB embeddings (extracted
later by `enrollment/enroll.py`) in the same metric space as the query
embeddings produced at runtime. Color/brightness/contrast normalization is
deliberately omitted — applying it here without applying it at runtime would
recreate the alignment-domain mismatch we just fixed.

Optional augmentation:
    --augment-flip   also save the horizontally flipped variant of each kept
                     face. Faces are roughly symmetric and MobileFaceNet was
                     trained with horizontal flip augmentation, so the flipped
                     embedding adds diversity without a domain shift.

Usage:
    python preprocess_dataset.py
    python preprocess_dataset.py --raw dataset_raw --out dataset_processed
    python preprocess_dataset.py --augment-flip
    python preprocess_dataset.py --quality 95
"""

from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from edge.align import align_face  # noqa: E402  shared with runtime

# --- Defaults (override via CLI) ---------------------------------------------
DEFAULT_RAW_DIR   = os.path.join(_PROJECT_ROOT, 'dataset_raw')
DEFAULT_OUT_DIR   = os.path.join(_PROJECT_ROOT, 'dataset_processed')
MODEL_DIR         = os.path.join(_PROJECT_ROOT, 'models')
DEFAULT_YUNET     = os.path.join(MODEL_DIR, 'yunet.onnx')

CANONICAL_SIZE    = 112       # MobileFaceNet input
WEBP_QUALITY      = 95        # near-lossless; ~3-5 KB per 112x112 face

SUPPORTED_EXTS    = {'.jpg', '.jpeg', '.png', '.webp'}

# --- Quality thresholds (intentionally lenient — small enrollment sets) ------
MIN_DET_SCORE     = 0.70      # YuNet confidence floor
MIN_FACE_PX       = 80        # bbox dimension floor
MIN_EYE_DIST_PX   = 20.0      # rough proxy for landmark resolvability
MIN_BLUR_LAPVAR   = 50.0      # rejects only severe motion blur
MAX_ASPECT        = 2.5       # bbox w/h sanity
MIN_ASPECT        = 0.4


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def list_identities(raw_dir: str) -> list[str]:
    if not os.path.isdir(raw_dir):
        return []
    return sorted(e.name for e in os.scandir(raw_dir) if e.is_dir())


def list_images(directory: str) -> list[str]:
    return sorted(
        e.path for e in os.scandir(directory)
        if e.is_file() and os.path.splitext(e.name)[1].lower() in SUPPORTED_EXTS
    )


def best_face(faces: np.ndarray | None) -> np.ndarray | None:
    """Largest-area face (presumed enrollment subject when multiple are visible)."""
    if faces is None or len(faces) == 0:
        return None
    if len(faces) == 1:
        return faces[0]
    areas = faces[:, 2] * faces[:, 3]
    return faces[int(np.argmax(areas))]


def quality_gate(f: np.ndarray, img_h: int, img_w: int) -> tuple[bool, str]:
    score = float(f[14]) if len(f) >= 15 else 1.0
    if score < MIN_DET_SCORE:
        return False, f'low_score({score:.2f})'

    x, y, w, h = int(f[0]), int(f[1]), int(f[2]), int(f[3])
    if w < MIN_FACE_PX or h < MIN_FACE_PX:
        return False, f'tiny_face({w}x{h})'

    ar = w / float(h + 1e-6)
    if ar < MIN_ASPECT or ar > MAX_ASPECT:
        return False, f'bad_aspect({ar:.2f})'

    if x + w <= 0 or y + h <= 0 or x >= img_w or y >= img_h:
        return False, 'oob'

    try:
        ex1, ey1 = float(f[4]), float(f[5])
        ex2, ey2 = float(f[6]), float(f[7])
        if float(np.hypot(ex2 - ex1, ey2 - ey1)) < MIN_EYE_DIST_PX:
            return False, 'narrow_eyes'
    except Exception:
        return False, 'bad_landmarks'

    return True, 'ok'


def laplacian_var(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# -----------------------------------------------------------------------------
# Core processor
# -----------------------------------------------------------------------------
class Preprocessor:
    """One-image-at-a-time face canonicalizer. Stateless across calls."""

    def __init__(self, yunet_path: str, canonical_size: int = CANONICAL_SIZE) -> None:
        self.detector = cv2.FaceDetectorYN.create(
            yunet_path, "", (320, 320), MIN_DET_SCORE, 0.30, 5000
        )
        self.canonical_size = canonical_size

    def process(self, img_bgr: np.ndarray) -> tuple[np.ndarray | None, str]:
        """
        Returns:
            (aligned_112x112_bgr, 'ok') on success
            (None, '<reason>')           on rejection
        """
        if img_bgr is None or img_bgr.size == 0:
            return None, 'empty_image'

        h, w = img_bgr.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img_bgr)
        f = best_face(faces)
        if f is None:
            return None, 'no_face'

        ok, reason = quality_gate(f, h, w)
        if not ok:
            return None, reason

        x, y, fw, fh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
        cx, cy = max(0, x), max(0, y)
        crop = img_bgr[cy:y + fh, cx:x + fw]
        if crop.size == 0:
            return None, 'empty_crop'

        # Blur on the crop (post-detection, pre-alignment)
        crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blur = laplacian_var(crop_gray)
        if blur < MIN_BLUR_LAPVAR:
            return None, f'blurry({blur:.0f})'

        # Localize landmarks to crop coordinates — matches runtime exactly.
        local_landmarks = [
            (int(f[4 + 2 * j]) - cx, int(f[4 + 2 * j + 1]) - cy)
            for j in range(5)
        ]
        aligned = align_face(crop, local_landmarks)
        if aligned is None or aligned.size == 0:
            return None, 'align_failed'

        if aligned.shape[:2] != (self.canonical_size, self.canonical_size):
            aligned = cv2.resize(aligned, (self.canonical_size, self.canonical_size))
        return aligned, 'ok'


# -----------------------------------------------------------------------------
# IO helpers
# -----------------------------------------------------------------------------
def save_webp(path: str, img_bgr: np.ndarray, quality: int) -> bool:
    return bool(cv2.imwrite(path, img_bgr, [cv2.IMWRITE_WEBP_QUALITY, int(quality)]))


def hflip(img_bgr: np.ndarray) -> np.ndarray:
    return cv2.flip(img_bgr, 1)


def wipe_old_outputs(out_id_dir: str) -> None:
    """Remove existing .webp files so a re-run produces a deterministic
    001.webp, 002.webp, ... sequence rather than mixing old + new."""
    if not os.path.isdir(out_id_dir):
        return
    for entry in os.listdir(out_id_dir):
        if entry.lower().endswith('.webp'):
            try:
                os.remove(os.path.join(out_id_dir, entry))
            except OSError:
                pass


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------
def run(raw_dir: str, out_dir: str, yunet_path: str,
        augment_flip: bool = False, quality: int = WEBP_QUALITY) -> None:

    if not os.path.isdir(raw_dir):
        print(f"[ERROR] raw_dir not found: {raw_dir}")
        return

    identities = list_identities(raw_dir)
    if not identities:
        print(f"[ERROR] No identity sub-directories under: {raw_dir}")
        return

    proc = Preprocessor(yunet_path)
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  Raw         : {raw_dir}")
    print(f"  Out         : {out_dir}")
    print(f"  Canonical   : {CANONICAL_SIZE}x{CANONICAL_SIZE} WEBP @ q{quality}")
    print(f"  Augmentation: flip={augment_flip}")
    print(f"  Identities  : {len(identities)} -> {identities}")
    print(f"{'=' * 60}\n")

    grand_in = grand_ok = grand_skip = 0
    skip_reasons: dict[str, int] = {}

    for ident in identities:
        in_dir  = os.path.join(raw_dir, ident)
        out_id  = os.path.join(out_dir, ident)
        os.makedirs(out_id, exist_ok=True)
        wipe_old_outputs(out_id)

        images = list_images(in_dir)
        if not images:
            print(f"--- {ident}: no supported images. SKIP.\n")
            continue

        print(f"--- {ident}: {len(images)} raw image(s) ---")
        idx = 1
        kept = skipped = 0

        for path in images:
            grand_in += 1
            img = cv2.imread(path)
            if img is None:
                print(f"  [SKIP] unreadable: {os.path.basename(path)}")
                grand_skip += 1
                skipped += 1
                skip_reasons['unreadable'] = skip_reasons.get('unreadable', 0) + 1
                continue

            aligned, reason = proc.process(img)
            if aligned is None:
                print(f"  [SKIP] {reason:>22}: {os.path.basename(path)}")
                grand_skip += 1
                skipped += 1
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue

            out_name = f"{idx:03d}.webp"
            ok = save_webp(os.path.join(out_id, out_name), aligned, quality)
            if not ok:
                print(f"  [SKIP] write_failed: {os.path.basename(path)}")
                grand_skip += 1
                skipped += 1
                skip_reasons['write_failed'] = skip_reasons.get('write_failed', 0) + 1
                continue
            print(f"  [OK]   {os.path.basename(path):>30} -> {out_name}")
            idx += 1
            kept += 1
            grand_ok += 1

            if augment_flip:
                flipped = hflip(aligned)
                aug_name = f"{idx:03d}.webp"
                if save_webp(os.path.join(out_id, aug_name), flipped, quality):
                    print(f"  [AUG]  {'flip':>30} -> {aug_name}")
                    idx += 1
                    kept += 1
                    grand_ok += 1

        print(f"  => kept {kept} canonical face(s), skipped {skipped}\n")

    print(f"{'=' * 60}")
    print(f"  Preprocessing complete.")
    print(f"  Inputs   : {grand_in}")
    print(f"  Stored   : {grand_ok}")
    print(f"  Skipped  : {grand_skip}")
    if skip_reasons:
        print(f"  Skip reasons:")
        for reason, count in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:>22}: {count}")
    print(f"{'=' * 60}\n")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Preprocess raw face images into canonical aligned 112x112 WEBP."
    )
    ap.add_argument('--raw',     default=DEFAULT_RAW_DIR,  help='raw dataset directory')
    ap.add_argument('--out',     default=DEFAULT_OUT_DIR,  help='processed output directory')
    ap.add_argument('--yunet',   default=DEFAULT_YUNET,    help='path to yunet.onnx')
    ap.add_argument('--quality', type=int, default=WEBP_QUALITY,
                    help='WEBP encoder quality (1-100, default 95)')
    ap.add_argument('--augment-flip', action='store_true',
                    help='also save horizontally flipped variant of each kept face')
    return ap.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(
        raw_dir=args.raw,
        out_dir=args.out,
        yunet_path=args.yunet,
        augment_flip=args.augment_flip,
        quality=args.quality,
    )
