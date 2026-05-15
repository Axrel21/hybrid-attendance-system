# Edge runtime

Python package and assets for the **on-device** attendance pipeline (Raspberry Pi or development PC).

## What lives here

- **Pipeline:** `main.py` (`FinalHybridEdge`) — YuNet, tracker, liveness, MobileFaceNet, hybrid offload client, diagnostics, optional frame telemetry, auto report hook.
- **Supporting modules:** `camera.py`, `liveness.py`, `align.py`, `orientation.py`, `pipeline_controller.py`, `tracker.py`, `telemetry.py`, `cloud_client.py`, `offload_router.py`, `experiment_report.py`, etc.

## Dependencies

- **Canonical pins:** `edge/requirements-edge.txt` (ARM-friendly; Conda/venv).
- **Legacy alias:** root `requirements_pi.txt` includes `-r edge/requirements-edge.txt`.

## Entry points

- **Production-style:** `python run.py` from repo root (creates experiment session, configures logging, runs pipeline).
- **Direct (advanced):** `python -m edge.main` — still valid; ensure `config.experiment_session` is initialized if you rely on per-run paths.

## Paths (unchanged)

- Models: `models/yunet.onnx`, `models/mobilefacenet.tflite` relative to **repo root** (see `_PROJECT_ROOT` in `edge/main.py`).
- Enrollment DB: `data/known_faces.json` (MobileFaceNet space — **not** mixed with ArcFace gallery on the server).

## Hybrid cloud

- Offload is **image-only** (JPEG crop). Env vars: `CLOUD_SERVER_URL`, `CLOUD_ROUTING`, `CLOUD_THRESHOLD`, `CLOUD_FORCE_OFFLOAD`, etc. See `cloud/README.md` for the API contract.

## Pi deployment bundle (goal)

A minimal device tree should include: `run.py`, `edge/`, `config/`, `edge/requirements-edge.txt`, `models/`, `data/known_faces.json` (and optional `deployment/pi/`). Omit `cloud/`, `research/`, raw datasets, and archived `experiments/` if you only need runtime (see `docs/DEPLOYMENT.md`).
