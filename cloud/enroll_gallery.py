"""
Gallery Enrollment Script
==========================

Used OFFLINE to build the ArcFace gallery on the cloud server.

Workflow:
  1. Collect enrollment images for each identity
  2. Run this script on the SERVER (or any machine with GPU)
  3. Generates gallery/ directory of .npy embedding files
  4. Server loads these at startup

This is intentionally separate from the live pipeline —
gallery enrollment is an offline research setup step.

Usage:
    python enroll_gallery.py \\
        --images_dir enrollment_images/ \\
        --gallery_dir gallery/ \\
        --model buffalo_l

Directory structure expected:
    enrollment_images/
        student_001/
            img_001.jpg
            img_002.jpg
        student_002/
            img_001.jpg
        ...
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import cv2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("enroll_gallery")


def enroll_gallery(images_dir: str, gallery_dir: str, model_name: str = "buffalo_l"):
    from arcface_verifier import ArcFaceVerifier

    images_path = Path(images_dir)
    gallery_path = Path(gallery_dir)
    gallery_path.mkdir(parents=True, exist_ok=True)

    if not images_path.exists():
        log.error(f"Images directory not found: {images_dir}")
        sys.exit(1)

    verifier = ArcFaceVerifier(model_name=model_name)
    enrolled = 0
    failed = 0

    for identity_dir in sorted(images_path.iterdir()):
        if not identity_dir.is_dir():
            continue

        identity = identity_dir.name
        images = list(identity_dir.glob("*.jpg")) + list(identity_dir.glob("*.png"))

        if not images:
            log.warning(f"No images found for '{identity}' — skipping")
            continue

        embeddings = []
        for img_path in images:
            img = cv2.imread(str(img_path))
            if img is None:
                log.warning(f"Could not read {img_path}")
                continue

            emb = verifier.extract_embedding(img)
            if emb is not None:
                embeddings.append(emb)
            else:
                log.warning(f"No face detected in {img_path}")

        if not embeddings:
            log.error(f"No valid embeddings for '{identity}' — skipping")
            failed += 1
            continue

        # Mean embedding across all enrollment images
        mean_emb = np.mean(embeddings, axis=0).astype(np.float32)
        mean_emb = mean_emb / (np.linalg.norm(mean_emb) + 1e-8)

        out_path = gallery_path / f"{identity}.npy"
        np.save(str(out_path), mean_emb)

        log.info(
            f"Enrolled '{identity}': {len(embeddings)} images → {out_path} "
            f"(failed: {len(images) - len(embeddings)})"
        )
        enrolled += 1

    log.info(f"\nDone — enrolled: {enrolled}, failed: {failed}")
    log.info(f"Gallery saved to: {gallery_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build ArcFace gallery from enrollment images")
    parser.add_argument("--images_dir", required=True, help="Root directory of enrollment images")
    parser.add_argument("--gallery_dir", default="gallery/", help="Output gallery directory")
    parser.add_argument("--model", default="buffalo_l", help="InsightFace model pack name")
    args = parser.parse_args()

    enroll_gallery(args.images_dir, args.gallery_dir, args.model)