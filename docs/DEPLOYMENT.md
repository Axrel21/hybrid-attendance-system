# Deployment instructions

## Classification

| Class | Paths (typical) | Deploy to Pi? | Deploy to cloud host? |
|-------|-------------------|---------------|-------------------------|
| Edge runtime | `run.py`, `edge/`, `config/`, `models/`, `data/known_faces.json` | **Yes** | No |
| Edge deps | `edge/requirements-edge.txt` or `requirements_pi.txt` | **Yes** | No |
| Pi systemd / notes | `deployment/pi/` | Copy as reference / install unit | No |
| Cloud runtime | `cloud/` + `cloud/gallery/*.npy` | **No** | **Yes** |
| Cloud deps | `cloud/requirements.txt` / `requirements_cloud.txt` | No | **Yes** |
| Research / analysis | `research/`, root shims, `enrollment/`, `preprocess` shim | No (optional on dev PC) | No |
| Experiment **outputs** | `experiments/exp_*` | Optional archive | Optional |
| Raw datasets | `dataset_raw/`, `dataset_processed/` | No | No |

**Invariant:** MobileFaceNet embeddings on the edge and ArcFace embeddings on the server are **never compared directly**. Offloading uses **JPEG face crops** only (`edge/cloud_client.py` ↔ `POST /verify/image`).

## Raspberry Pi (Conda / Miniforge)

1. Clone repo on the Pi.
2. Activate your Conda env (Python 3.10+).
3. `pip install -r edge/requirements-edge.txt`
4. Place `models/yunet.onnx` and `models/mobilefacenet.tflite` under repo `models/`.
5. Place `data/known_faces.json` (from enrollment pipeline on a dev machine).
6. Run: `HEADLESS=1 CAMERA_BACKEND=libcamera python run.py` (adjust env per `config/settings.py` / `run.py` docstring).
7. Optional: install `deployment/pi/attendance.service` (edit `User`, `WorkingDirectory`, `ExecStart`).

### Selective copy example (rsync)

From dev machine (adjust paths):

```bash
rsync -av --relative \
  ./run.py \
  ./edge/ ./config/ ./models/ ./data/known_faces.json \
  ./deployment/pi/ \
  pi@raspberrypi:~/attendance/
```

Add `EXPERIMENT_LABEL`, `CLOUD_SERVER_URL`, etc. via systemd `Environment=` or shell profile as needed. **Do not** sync `cloud/`, `dataset_raw/`, or `research/` if the Pi is edge-only.

## Cloud server

1. `cd cloud && python -m venv .venv && source .venv/bin/activate` (or Conda).
2. `pip install -r requirements.txt` (GPU: `onnxruntime-gpu`; CPU: swap per README).
3. Build `gallery/` with `enroll_gallery.py` **before** `uvicorn`.
4. Run from **`cloud/`** working directory:

```bash
cd cloud
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Shared config

`config/` at repo root is shared metadata for the **edge** and **research** tooling. The FastAPI server does not import it.

See also: `deployment/README.md`, `shared/README.md`, `edge/README.md`, `cloud/README.md`.
