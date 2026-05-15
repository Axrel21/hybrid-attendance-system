# Validation checklists (post-refactor)

Run from **repository root** with the appropriate venv/Conda env activated.

## 1. Imports compile

```bash
python -m compileall -q config edge cloud research experiments run.py preprocess_dataset.py
```

## 2. Edge runtime launches

```bash
# Smoke: may exit quickly if no camera — goal is no ImportError
python -c "from edge.main import FinalHybridEdge; print('edge import OK')"
python -c "import run; print('run.py import OK')"
```

Full run: `python run.py` (requires camera/models).

## 3. Cloud runtime launches

```bash
cd cloud
python -c "from gallery import FaceGallery; from arcface_verifier import ArcFaceVerifier"
```

Full run: `uvicorn main:app --host 127.0.0.1 --port 8000` (needs deps + optional empty `gallery/`).

## 4. Telemetry CSVs

- Run a short `run.py` session with `TELEMETRY=1`.
- Confirm under `experiments/exp_*/telemetry/telemetry_log.csv` and `.../diagnostics/diagnostic_log.csv` with expected headers (schema rotation should not trigger on clean session).

## 5. Experiment reports

- With `AUTO_EXPERIMENT_REPORT=1` and pandas+matplotlib installed, stop pipeline normally; check `experiments/exp_*/plots/` and `summaries/`.

## 6. Plots / analysis

```bash
python analyze_orientation.py --help
python analyze_pi_perf.py --help
python -m research.analysis.liveness_diag_print --help
```

## 7. Routing telemetry

- In diagnostic CSV, rows with `decision=OFFLOAD_TO_CLOUD` should appear when mid-threshold matches occur and router permits offload (env-dependent).

## 8. Offload telemetry

- Columns `cloud_outcome`, `cloud_rtt_ms`, `jpeg_encode_ms`, etc. populated when cloud is reachable.

## 9. Cloud verification

- With server up: set `CLOUD_SERVER_URL` on edge; trigger mid-confidence face; HTTP 200 from `/verify/image` and non-null `cloud_rtt_ms` in diagnostics.

## 10. Experiment session tracking

- After `run.py`, `EXPERIMENT_ROOT` and `experiments/exp_*` exist; `config/settings_snapshot.json` written.

## Reporting validation

- Same as §5–6; `edge/experiment_report.py` unchanged in behavior.
- Orientation launcher: `python -m experiments.run_orientation_experiment --help`
