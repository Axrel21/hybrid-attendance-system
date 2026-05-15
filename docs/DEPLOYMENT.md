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

### Selective copy (scripted)

The second-pass stabilization adds a rsync `--files-from` manifest and a
small wrapper that defaults to `--dry-run`:

```bash
# Preview (no copy)
bash deployment/pi/deploy_pi.sh pi@raspberrypi:~/attendance/

# Real copy
bash deployment/pi/deploy_pi.sh --apply pi@raspberrypi:~/attendance/
```

The manifest is plain text — `deployment/pi/PI_BUNDLE.txt` — so it is
easy to audit before any rsync runs. It includes `run.py`, `edge/`,
`config/`, `deployment/pi/`, `data/known_faces.json`, and the two model
files under `models/`. It **excludes** `cloud/`, `research/`,
`dataset_raw/`, archived `experiments/`, and `data/plots/`.

### Selective copy (manual)

The equivalent manual rsync (kept here for reference):

```bash
rsync -av --relative \
  ./run.py \
  ./edge/ ./config/ ./models/ ./data/known_faces.json \
  ./deployment/pi/ \
  pi@raspberrypi:~/attendance/
```

Add `EXPERIMENT_LABEL`, `CLOUD_SERVER_URL`, etc. via systemd `Environment=` or shell profile as needed. **Do not** sync `cloud/`, `dataset_raw/`, or `research/` if the Pi is edge-only.

## Cloud server

1. From the dev machine, copy with the manifest:

```bash
bash deployment/cloud/deploy_cloud.sh user@server:~/arcface_server/   # dry-run
bash deployment/cloud/deploy_cloud.sh --apply user@server:~/arcface_server/
```

2. On the server: `cd cloud && python -m venv .venv && source .venv/bin/activate` (or Conda).
3. `pip install -r requirements.txt` (GPU: `onnxruntime-gpu`; CPU: swap per README).
4. Build `gallery/` with `enroll_gallery.py` **before** `uvicorn`.
5. Run from **`cloud/`** working directory:

```bash
cd cloud
uvicorn main:app --host 0.0.0.0 --port 8000
```

`deploy_cloud.sh` excludes `cloud/gallery/` and `cloud/.venv/` — the
server must build its own with its enrollment images. See
`deployment/cloud/README.md`.

## Shared config

`config/` at repo root is shared metadata for the **edge** and **research** tooling. The FastAPI server does not import it.

See also: `deployment/README.md`, `shared/README.md`, `edge/README.md`, `cloud/README.md`.
