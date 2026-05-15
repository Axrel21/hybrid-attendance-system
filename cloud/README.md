# ArcFace Cloud Verification Server

Cloud-side component of the Hybrid Edge–Cloud Facial Recognition Attendance System.

**Repository layout:** Server code lives under `cloud/`. Install dependencies via `pip install -r cloud/requirements.txt` or root `requirements_cloud.txt` (includes that file). Run `uvicorn` with **working directory = `cloud/`** so `gallery/` resolves next to `main.py`.

Receives JPEG face crops from the Raspberry Pi edge device, runs ArcFace embedding
extraction server-side, and returns a structured verification result with per-stage
latency telemetry.

---

## Contents

- [Architecture in one paragraph](#architecture-in-one-paragraph)
- [Critical invariant — why images, not embeddings](#critical-invariant--why-images-not-embeddings)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Gallery enrollment](#gallery-enrollment)
- [Running the server](#running-the-server)
- [API reference](#api-reference)
- [Verification threshold calibration](#verification-threshold-calibration)
- [Model selection](#model-selection)
- [Latency profile](#latency-profile)
- [File structure](#file-structure)
- [Troubleshooting](#troubleshooting)
- [Research notes](#research-notes)

---

## Architecture in one paragraph

The edge device (Raspberry Pi 4) runs YuNet detection and MobileFaceNet for
lightweight recognition. When edge confidence falls below the offload threshold,
the aligned face crop is JPEG-encoded and sent here via `POST /verify/image`
(multipart/form-data). This server decodes the image, extracts a 512-d ArcFace
embedding via InsightFace, performs cosine similarity search against a pre-enrolled
gallery of ArcFace embeddings, and returns identity + confidence + per-stage
latency breakdown. The edge device receives the result within its 2-second timeout
budget and logs the hybrid telemetry. If the server is unreachable, the edge circuit
breaker kicks in and falls back to the local MobileFaceNet decision.

---

## Critical invariant — why images, not embeddings

**The edge sends a JPEG image. It never sends a MobileFaceNet embedding.**

This is a hard architectural rule, not a preference. The reason:

MobileFaceNet and ArcFace were trained with different loss functions, on different
data, with different architectures. They produce embeddings that live in **different
geometric spaces**. A 128-d MobileFaceNet vector and a 512-d ArcFace vector are not
comparable by any distance metric — cosine similarity between them is meaningless.

If you send a MobileFaceNet embedding to this server and compare it against an
ArcFace gallery, you will get a number back, but it will be noise. The gallery
search will return an identity, but it will be random. There is no error message
because the math is syntactically valid; the invalidity is semantic.

The correct design is:
- Edge: detect, align, crop, run MobileFaceNet locally for confidence estimate
- Cloud: receive the same crop, extract ArcFace independently, compare ArcFace vs ArcFace

The gallery is enforced to contain only 512-d embeddings. Both `FaceGallery.enroll()`
and `FaceGallery.search()` raise `ValueError` if a non-512-d vector is passed,
so the contamination path is hard-blocked at runtime.

---

## Prerequisites

**Python:** 3.10 or 3.11 recommended. InsightFace has packaging issues on 3.12
as of mid-2025; check the InsightFace issue tracker before upgrading.

**OS:** Linux (Ubuntu 22.04 or 24.04). macOS works for development with CPU-only
inference. Windows is untested and not recommended.

**For GPU inference (recommended for production use):**
- NVIDIA GPU with CUDA 11.8 or 12.x
- Matching cuDNN version
- `onnxruntime-gpu` instead of `onnxruntime`

**For CPU-only (development / low-budget cloud):**
- Any machine with enough RAM (~4 GB minimum for `buffalo_l`)
- Replace `onnxruntime-gpu` with `onnxruntime` in requirements.txt

**Model download:** InsightFace downloads model weights automatically on first run
from a CDN. The `buffalo_l` pack is approximately 700 MB. The server machine needs
internet access on first startup, or you must pre-download and place models manually
at `~/.insightface/models/buffalo_l/`.

---

## Installation

```bash
# 1. Clone or copy this directory to your server
cd cloud_server/

# 2. Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Verify InsightFace can load
python -c "from insightface.app import FaceAnalysis; print('InsightFace OK')"
```

If you are on a **CPU-only machine**, edit `requirements.txt` before step 3:
replace `onnxruntime-gpu` with `onnxruntime`.

If you get a CUDA provider error at runtime but want CPU fallback, the server
handles this automatically — `ArcFaceVerifier` passes
`["CUDAExecutionProvider", "CPUExecutionProvider"]` to ONNX Runtime, which will
fall back to CPU if CUDA is unavailable.

---

## Gallery enrollment

The gallery must be built **before** starting the server. This is an offline step.
The server loads `.npy` embedding files from `gallery/` at startup.

### Step 1 — Prepare enrollment images

```
enrollment_images/
    student_001/
        front_001.jpg
        slight_left.jpg
        slight_right.jpg
    student_002/
        front_001.jpg
    ...
```

- Directory name = identity label used in all downstream telemetry and responses
- More images per identity = more robust mean embedding (3–5 recommended, 1 minimum)
- Images should be reasonably frontal, well-lit, unoccluded
- Any resolution works — InsightFace handles resizing internally

### Step 2 — Run enrollment

```bash
python enroll_gallery.py \
    --images_dir enrollment_images/ \
    --gallery_dir gallery/ \
    --model buffalo_l
```

This will print per-identity results and a summary:

```
2026-05-12T14:30:22 [INFO] Loading InsightFace model pack 'buffalo_l' ...
2026-05-12T14:30:31 [INFO] InsightFace loaded in 8432.1 ms
2026-05-12T14:30:31 [INFO] Enrolled 'student_001': 3 images → gallery/student_001.npy (failed: 0)
2026-05-12T14:30:31 [INFO] Enrolled 'student_002': 1 images → gallery/student_002.npy (failed: 0)
...
2026-05-12T14:30:35 [INFO] Done — enrolled: 24, failed: 0
```

### Step 3 — Verify gallery

```bash
ls gallery/        # should show one .npy per identity
python -c "
import numpy as np, glob
files = sorted(glob.glob('gallery/*.npy'))
for f in files:
    e = np.load(f)
    print(f'{f}: shape={e.shape} norm={np.linalg.norm(e):.4f}')
"
```

All embeddings should be shape `(512,)` with norm close to `1.0` (they are
L2-normalised). If you see `(128,)`, the wrong model was used for enrollment —
delete those files and re-run with `buffalo_l`.

### Important: do not mix enrollment sources

All `.npy` files in `gallery/` must have been extracted by the same ArcFace model.
If you enroll some identities with `buffalo_l` and others with `buffalo_sc`, gallery
search scores will be inconsistent across identities. Pick one model and use it for
everything.

---

## Running the server

```bash
# Activate your venv if not already active
source .venv/bin/activate

# Start with uvicorn (development)
uvicorn main:app --host 0.0.0.0 --port 8000

# Production: add workers and log level
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
```

> **Note on workers:** Keep `--workers 1`. InsightFace's ONNX Runtime session
> is not designed for multi-process sharing. Multiple workers would load multiple
> copies of the model (~700 MB each) and cause CUDA context conflicts. For this
> research scale, a single async worker is correct.

On startup you should see:

```
2026-05-12T14:31:00 [INFO] arcface_server: === ArcFace Server startup ===
2026-05-12T14:31:00 [INFO] arcface_verifier: Loading InsightFace model pack 'buffalo_l' ...
2026-05-12T14:31:08 [INFO] arcface_verifier: InsightFace loaded in 8215.3 ms
2026-05-12T14:31:08 [INFO] face_gallery: Loaded 24 identities from 'gallery/'
2026-05-12T14:31:08 [INFO] arcface_server: Models loaded in 8831.4 ms — 24 identities enrolled
```

Model load time is 5–15 seconds on first run (weights load from disk into ONNX Runtime).
Subsequent requests take 30–80 ms on GPU, 150–400 ms on CPU.

### Smoke test

```bash
# Health check
curl http://localhost:8000/health
# → {"status":"ok","gallery_size":24,"model_loaded":true}

# Gallery contents
curl http://localhost:8000/gallery/stats
# → {"total_identities":24,"identities":["student_001","student_002",...]}

# Test with a face image (replace face.jpg with an actual face crop)
curl -X POST http://localhost:8000/verify/image \
  -F "image=@face.jpg" \
  -F 'metadata={"session_id":"test","frame_id":1,"edge_confidence":0.55,"edge_candidate":"student_001","timestamp_edge_ms":1234567890}'
```

---

## API reference

### `GET /health`

Returns server status. Used by the edge circuit breaker.

```json
{
  "status": "ok",
  "gallery_size": 24,
  "model_loaded": true
}
```

### `POST /verify/image`

Primary verification endpoint. Called by `CloudVerificationClient` on the edge
when MobileFaceNet confidence falls below the offload threshold.

**Request:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `image` | File (JPEG) | Aligned face crop from edge detector |
| `metadata` | string (JSON) | Verification context — see below |

**Metadata JSON fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | yes | Experiment session identifier |
| `frame_id` | int | yes | Frame counter from edge pipeline |
| `track_id` | int | no | Tracker ID if available |
| `edge_confidence` | float | yes | MobileFaceNet score that triggered this offload |
| `edge_candidate` | string or null | no | Edge's best-guess identity |
| `routing_strategy` | string | no | Which router made this decision |
| `timestamp_edge_ms` | int | yes | Edge Unix timestamp in milliseconds |

**Response:**

```json
{
  "verified": true,
  "identity": "student_042",
  "arcface_confidence": 0.873241,
  "edge_candidate": "student_042",
  "edge_cloud_agree": true,
  "image_decode_ms": 1.8,
  "arcface_extract_ms": 34.2,
  "gallery_search_ms": 0.4,
  "server_total_ms": 38.7,
  "route": "CLOUD_VERIFY",
  "request_id": "a3f9b12c",
  "timestamp_server_ms": 1747043422901,
  "gallery_size": 24
}
```

When `verified` is `false`, `identity` is `null`. The score is still returned so
you can analyse near-threshold events in telemetry.

When ArcFace finds no face in the received crop (degenerate case — severe blur or
occlusion), the server returns `verified: false` with `arcface_confidence: 0.0`
rather than raising an error. This is intentional: it surfaces as a researchable
event in the telemetry CSV rather than causing the edge to crash.

### `POST /enroll`

Offline enrollment via REST. Accepts 512-d ArcFace embeddings only.
Prefer `enroll_gallery.py` for bulk enrollment; this endpoint exists for
programmatic single-identity additions.

```json
{
  "identity": "student_025",
  "embedding": [0.023, -0.114, ...],
  "source": "enrollment_session_2"
}
```

Will return HTTP 422 if the embedding is not 512-d with a clear error message.

### `GET /gallery/stats`

Returns enrolled identity list. Useful for verifying gallery state before
starting an experiment.

---

## Verification threshold calibration

The current threshold is `0.35` (cosine similarity), set in `main.py`:

```python
VERIFICATION_THRESHOLD = 0.35
```

This is a conservative starting value. The right threshold depends on your
specific gallery population, lighting conditions, and acceptable FAR/FRR tradeoff.

**How to calibrate it properly:**

1. After enrollment, run a probe session with known-identity faces
2. Collect the `arcface_confidence` values from the telemetry CSV for correct matches
3. Separately collect scores for non-enrolled faces (impostor probes)
4. Plot the two score distributions and find the EER (equal error rate) point
5. Adjust based on your application's preference for FAR vs FRR

For a controlled classroom attendance scenario, a threshold between `0.30` and
`0.45` is typical for ArcFace r100 / buffalo_l. Below `0.25` you will get frequent
false rejects. Above `0.50` you risk false accepts depending on gallery population.

The threshold is intentionally hardcoded in `main.py` rather than a config file
so that it is version-controlled and experiment sessions have a clear record of
what threshold was active. If you want to sweep thresholds experimentally, change
it and restart the server for each sweep condition.

---

## Model selection

`ArcFaceVerifier` defaults to `buffalo_l`. The relevant tradeoffs:

| Model | Size | Accuracy | Inference (GPU) | Inference (CPU) | Use case |
|-------|------|----------|----------------|----------------|----------|
| `buffalo_l` | ~700 MB | High | 30–60 ms | 200–400 ms | Research default |
| `buffalo_sc` | ~100 MB | Medium | 15–30 ms | 80–150 ms | Budget CPU cloud |
| `antelopev2` | ~500 MB | Higher | 40–80 ms | 300–500 ms | Higher accuracy |

To change model: edit `ArcFaceVerifier.__init__()` default, re-run enrollment
with the same model, and restart the server. **Never mix enrollment model and
inference model.**

InsightFace downloads models to `~/.insightface/models/` on first use. To
pre-cache on a machine without internet:

```bash
# On internet-connected machine
python -c "
from insightface.app import FaceAnalysis
app = FaceAnalysis(name='buffalo_l')
app.prepare(ctx_id=0)
print('Downloaded to ~/.insightface/models/buffalo_l/')
"
# Then copy ~/.insightface/models/buffalo_l/ to the server
```

---

## Latency profile

Typical server-side breakdown when measured via the `server_total_ms` response field:

| Stage | GPU (A100/V100) | GPU (T4/3090) | CPU (8-core) |
|-------|----------------|--------------|-------------|
| Image decode | 1–3 ms | 1–3 ms | 1–3 ms |
| ArcFace extraction | 15–35 ms | 30–60 ms | 150–350 ms |
| Gallery search (N=50) | <1 ms | <1 ms | <1 ms |
| **Server total** | **18–40 ms** | **32–65 ms** | **155–355 ms** |

Gallery search is brute-force matrix multiplication and is negligible at research
scale (N < 200 identities). At N=1000 it would still be <5 ms on CPU.

The edge client has a 2-second hard timeout. Even the slowest CPU path should
complete well within this budget under normal load. Timeout risk increases when
the server is under concurrent load or memory pressure (swap usage).

**Total round-trip time** (edge-measured `rtt_ms`) adds network latency to
`server_total_ms`. On a LAN this is typically +5–20 ms. Over the internet or
WiFi with congestion it can be +50–200 ms and becomes the dominant latency source.

---

## File structure

```
cloud_server/
├── main.py               # FastAPI app, /verify/image endpoint, /enroll, /health
├── arcface_verifier.py   # InsightFace wrapper — stateless, handles model load + extraction
├── gallery.py            # FaceGallery — enrollment store, cosine search, .npy persistence
├── enroll_gallery.py     # Offline enrollment script — run once before starting server
├── requirements.txt      # Server-side Python dependencies
├── README.md             # This file
└── gallery/              # Created by enroll_gallery.py — one .npy per identity
    ├── student_001.npy
    ├── student_002.npy
    └── ...
```

`enrollment_images/` is not part of this directory — it is your input data,
kept separately from the serving code.

---

## Troubleshooting

**`ImportError: No module named 'insightface'`**
You are not in the virtual environment, or `pip install -r requirements.txt`
failed silently. Run `pip show insightface` to check.

**`onnxruntime.capi.onnxruntime_pybind11_state.InvalidGraph`**
Version mismatch between `insightface` and `onnxruntime`. The versions pinned in
`requirements.txt` are tested together. If you upgraded either independently,
downgrade back to the pinned versions.

**Server starts but `gallery_size: 0` in `/health`**
The `gallery/` directory was not found relative to the working directory where you
ran `uvicorn`. Run uvicorn from inside `cloud_server/`, not from the parent
directory. Or pass an absolute path to `gallery.load_from_disk()` in `main.py`.

**`FaceGallery.enroll: expected 512-d ArcFace embedding, got 128-d`**
The enrollment script ran with a wrong model, or a `.npy` file from the old
embedding-based architecture is present in `gallery/`. Delete all `.npy` files in
`gallery/` and re-run `enroll_gallery.py` with `--model buffalo_l`.

**ArcFace returns `verified: false` for all faces**
Three likely causes, in order of probability:
1. Gallery was enrolled with a different model than the running server — re-enroll
2. Threshold is too high — check `arcface_confidence` in the response; if it is
   0.20–0.30 for correct identities, lower `VERIFICATION_THRESHOLD`
3. Enrollment images were poor quality (heavy occlusion, extreme angles) — 
   re-enroll with better images

**`cv2.imdecode failed` errors in server log**
The edge is sending malformed or empty JPEG bytes. Check `jpeg_encode_ms` in the
edge telemetry — if it is 0.0 or the `encode_failed` outcome appears, the edge
`cv2.imencode()` call is failing, likely because the face crop array is empty or
has an unexpected shape.

**High `arcface_extract_ms` (>500 ms)**
CUDA is not being used — ONNX Runtime fell back to CPU. Check that:
- `onnxruntime-gpu` is installed (not plain `onnxruntime`)
- Your CUDA version matches the `onnxruntime-gpu` build
- Run `python -c "import onnxruntime as rt; print(rt.get_device())"` — should print `GPU`

---

## Research notes

**Gallery is loaded at startup and held in memory.** Changes to `gallery/`
after the server starts are not picked up without a restart. This is intentional —
gallery state must be stable across an experiment session for results to be
reproducible.

**The gallery uses mean-face enrollment.** If you call `enroll()` with the same
identity multiple times, the embedding is averaged with the existing one rather
than replaced. This is implemented in `FaceGallery.enroll()`. The rationale is
that averaging across multiple enrollment images produces a more centred embedding
in ArcFace space, which tends to improve recall for that identity.

**`top_k_search()` exists but is not wired to the API.** It is available in
`FaceGallery` for offline analysis — useful for post-hoc inspection of score
distributions and for understanding near-miss events. Call it directly in analysis
scripts that load the gallery.

**The verification threshold is not a hyperparameter to tune online.** It belongs
in experiment config. Change it, restart the server, run a full session, compare
results across threshold values. Changing it mid-session invalidates the session
for comparative analysis.

**Edge/cloud agreement rate is a diagnostic, not a performance metric.** A high
agreement rate means the edge and cloud reach the same identity decision on
offloaded frames — good. A low agreement rate means either the edge is consistently
wrong on low-confidence frames (expected and fine — that is why you offload) or
there is a systematic problem (threshold miscalibration, gallery quality, crop
alignment drift between edge and cloud). Investigate with the `arcface_confidence`
and `edge_confidence` columns in `hybrid_telemetry.csv`.