# enrollment/enroll.py
"""
Enrollment is now strictly an embedding step. Detection, validation, cropping,
and 5-point alignment all happen upstream in `preprocess_dataset.py`, which
writes canonical 112x112 WEBP face crops to `dataset_processed/<identity>/`.

This script:
    1. scans dataset_processed/<identity>/*.webp
    2. runs MobileFaceNet on each canonical crop
    3. L2-normalizes each embedding
    4. populates BOTH `frontal` and `angled` pools (matches the runtime
       pose_aware_match() routing — overhead/tilted modes query 'angled' first)
    5. writes data/known_faces.json **from scratch** (no merge with stale data)

Usage:
    python -m enrollment.enroll
"""

from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np

# TFLite runtime shim: use the lightweight tflite-runtime package on Pi
# (ARM64, no full TF wheel available on PyPI); fall back to the full
# tensorflow package on the development machine.
try:
    from tflite_runtime.interpreter import Interpreter as TFLiteInterpreter
except ImportError:
    import tensorflow as tf
    TFLiteInterpreter = tf.lite.Interpreter

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT  = os.path.abspath(os.path.join(BASE_DIR, '..'))
MODEL_DIR     = os.path.join(PROJECT_ROOT, 'models')
DATA_DIR      = os.path.join(PROJECT_ROOT, 'data')
PROCESSED_DIR = os.path.join(PROJECT_ROOT, 'dataset_processed')

CANONICAL_SIZE = 112


class Enroller:
    """Thin wrapper around MobileFaceNet TFLite. No YuNet, no alignment —
    those happen in preprocess_dataset.py."""

    def __init__(self, tflite_path: str) -> None:
        self.interpreter = TFLiteInterpreter(model_path=tflite_path)
        self.interpreter.allocate_tensors()
        self.input_idx  = self.interpreter.get_input_details()[0]['index']
        self.output_idx = self.interpreter.get_output_details()[0]['index']
        from shared.contracts import is_valid_mobilefacenet_dim
        _out_dim = int(self.interpreter.get_output_details()[0]['shape'][-1])
        assert is_valid_mobilefacenet_dim(_out_dim), (
            f"Unexpected embedding dim {_out_dim}. "
            f"Enrollment and runtime must use the same model."
        )

    def extract_embedding(self, face_bgr: np.ndarray) -> list[float]:
        """Expects a canonical 112x112 BGR aligned face (as produced by
        preprocess_dataset.py). Resizes defensively if the input differs."""
        if face_bgr.shape[:2] != (CANONICAL_SIZE, CANONICAL_SIZE):
            face_bgr = cv2.resize(face_bgr, (CANONICAL_SIZE, CANONICAL_SIZE))
        x = (np.float32(face_bgr) - 127.5) / 128.0
        x = np.expand_dims(x, axis=0)
        self.interpreter.set_tensor(self.input_idx, x)
        self.interpreter.invoke()
        emb = self.interpreter.get_tensor(self.output_idx)[0]
        return (emb / np.linalg.norm(emb)).tolist()


# -----------------------------------------------------------------------------
# IO
# -----------------------------------------------------------------------------
def list_processed(processed_dir: str) -> dict[str, list[str]]:
    """Return {identity: [webp_path, ...]} from dataset_processed/."""
    out: dict[str, list[str]] = {}
    if not os.path.isdir(processed_dir):
        return out
    for entry in sorted(os.scandir(processed_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        webps = sorted(
            p.path for p in os.scandir(entry.path)
            if p.is_file() and p.name.lower().endswith('.webp')
        )
        if webps:
            out[entry.name] = webps
    return out


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------
def build_db(processed_dir: str, tflite_path: str, out_path: str) -> None:
    identities = list_processed(processed_dir)
    if not identities:
        print(f"[ERROR] No identities under {processed_dir}.")
        print(f"        Run `python preprocess_dataset.py` first.")
        return

    print(f"\n{'=' * 60}")
    print(f"  Source : {processed_dir}")
    print(f"  Output : {out_path}")
    print(f"  Identities: {len(identities)}")
    for name, paths in identities.items():
        print(f"    {name}: {len(paths)} canonical face(s)")
    print(f"{'=' * 60}\n")

    enroller = Enroller(tflite_path)
    db: dict[str, dict[str, list]] = {}
    total_ok = total_skip = 0

    for name, paths in identities.items():
        print(f"--- Embedding: {name} ({len(paths)} face(s)) ---")
        embs: list[list[float]] = []
        for path in paths:
            img = cv2.imread(path)
            if img is None:
                print(f"  [SKIP] unreadable: {os.path.basename(path)}")
                total_skip += 1
                continue
            try:
                emb = enroller.extract_embedding(img)
            except Exception as e:
                print(f"  [SKIP] embed_failed ({e}): {os.path.basename(path)}")
                total_skip += 1
                continue
            embs.append(emb)
            total_ok += 1
            print(f"  [OK]   {os.path.basename(path)}")

        # Replicate into both pose pools so pose_aware_match never queries
        # an empty list (its OVERHEAD/TILTED branch hits 'angled' first).
        db[name] = {"frontal": list(embs), "angled": list(embs)}
        print(f"  => stored {len(embs)} embeddings (frontal & angled)\n")

    # Source of truth is dataset_processed/. We do NOT merge with any prior
    # known_faces.json — preprocessing is now the canonical input.
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as fh:
        json.dump(db, fh)

    print(f"{'=' * 60}")
    print(f"  Enrollment complete.")
    print(f"  Identities : {len(db)}")
    print(f"  Embeddings : {total_ok}")
    print(f"  Skipped    : {total_skip}")
    print(f"  Output     : {out_path}")
    print(f"{'=' * 60}\n")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    tflite_path = os.path.join(MODEL_DIR, 'mobilefacenet.tflite')
    out_path    = os.path.join(DATA_DIR,  'known_faces.json')
    build_db(PROCESSED_DIR, tflite_path, out_path)
