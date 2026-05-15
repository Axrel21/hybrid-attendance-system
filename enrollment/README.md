# `enrollment/` — dev-time enrollment for the edge

Offline step that builds `data/known_faces.json` (MobileFaceNet
embeddings) from `dataset_processed/<identity>/*.webp`.

**Not part of the Pi deployment bundle.** Run on a development machine
with the edge stack installed, then copy the resulting
`data/known_faces.json` to the Pi (see `docs/DEPLOYMENT.md`).

## Pipeline position

```
dataset_raw/<identity>/*.{jpg,png,webp}
    │
    ▼  python preprocess_dataset.py     (research/dataset_preprocess.py)
dataset_processed/<identity>/*.webp     (112x112 aligned)
    │
    ▼  python -m enrollment.enroll
data/known_faces.json                   (runtime artifact for edge.main)
```

`enrollment/enroll.py` only embeds. Detection, validation, cropping, and
the 5-point alignment all happen upstream in `preprocess_dataset.py`,
which shares `edge.align.align_face` with the runtime path so the
enrollment and query embeddings live in the same metric space.

## Cloud equivalent

The ArcFace gallery on the cloud server is built independently by
`cloud/enroll_gallery.py` (different embedding model, different on-disk
format). The two enrollment paths are deliberately separate to enforce
the no-cross-model-comparison invariant.

## Why not under `research/`?

`docs/DEPLOYMENT.md` classifies `enrollment/` as research/dev tooling.
Keeping it at the repo root preserves the existing
`python -m enrollment.enroll` invocation and the
`enrollment/enroll.py:DATA_DIR` constant referenced from external
notebooks. The second-pass stabilization documents the role; moving the
package is deferred to avoid breaking those entry points.
