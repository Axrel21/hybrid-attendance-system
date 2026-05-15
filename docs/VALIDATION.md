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

## 11. Second-pass artifacts

- Selective deploy dry-runs (no destination side-effects):

```bash
bash deployment/pi/deploy_pi.sh user@pi:~/attendance/
bash deployment/cloud/deploy_cloud.sh user@server:~/arcface_server/
```

- Legacy alias still resolves to the edge stack:

```bash
pip install --dry-run -r requirments.txt   # expands to edge/requirements-edge.txt
```

- Session index appears after a short `run.py` smoke session:

```bash
ls experiments/index.jsonl
tail -n 1 experiments/index.jsonl   # one JSON line per run
```

- Edge import smoke test still includes the new path:

```bash
python -c "from config.experiment_session import _append_session_index, init_experiment_session"
```

## 12. Third-pass artifacts

- `shared/` is importable without the edge runtime stack installed:

```bash
python -c "
import shared
from shared import (
    VERIFY_IMAGE_PATH, METADATA_FIELDS, VERIFICATION_RESPONSE_FIELDS,
    ARCFACE_EMBEDDING_DIM, DEFAULT_JPEG_QUALITY, ATTENDANCE_CSV_COLUMNS,
    EXPERIMENT_INDEX_FIELDS,
)
assert ARCFACE_EMBEDDING_DIM == 512
print('shared OK')
"
```

- Lazy CSV schema access fails cleanly when edge runtime deps are
  missing, succeeds when they are installed:

```bash
python -c "
from shared.schemas import get_diag_columns, get_telemetry_csv_columns
try:
    cols = get_diag_columns()
    print('DIAG cols:', len(cols))
except ImportError as exc:
    print('expected on hosts without cv2/tflite:', exc)
"
```

- Manifest sanity check (CI-safe, dry-runs only):

```bash
bash deployment/common/verify_manifests.sh
```

- Tarball builds:

```bash
bash deployment/common/package_pi.sh /tmp/_dist
bash deployment/common/package_cloud.sh /tmp/_dist
tar -tzf /tmp/_dist/attendance_pi_*.tar.gz | head
tar -tzf /tmp/_dist/arcface_server_*.tar.gz | head
```

## 13. System completion phase artifacts

- Composite backend launches (requires `cloud/requirements.txt`
  installed):

```bash
# Bare verification (legacy)
cd cloud && uvicorn main:app --host 0.0.0.0 --port 8000

# Composite (verification + telemetry + dashboard + WS)
bash deployment/cloud/run_backend.sh --host 0.0.0.0 --port 8000
```

- Sanity GET after composite launch:

```bash
curl -s http://localhost:8000/backend/info | python3 -m json.tool
curl -s http://localhost:8000/telemetry/healthz | python3 -m json.tool
curl -s http://localhost:8000/api/sessions | python3 -m json.tool
```

- Edge uploader dry-run against an existing session directory:

```bash
python3 -m edge.telemetry_uploader \
    --session experiments/exp_<id>/ \
    --cloud http://localhost:8000 --dry-run
```

- End-to-end uploader (real POST):

```bash
python3 -m edge.telemetry_uploader \
    --session experiments/exp_<id>/ \
    --cloud http://localhost:8000
ls cloud_storage/sessions/exp_<id>/   # metadata.json events.jsonl summary.json
```

- WebSocket smoke (requires `websocat` or similar):

```bash
websocat ws://localhost:8000/ws/telemetry
# expect: {"type":"hello","session_filter":null,...}
# subsequent /telemetry/ingest POSTs should produce telemetry_batch frames
```

- Storage and analytics smoke (numpy only — no fastapi required):

```bash
python3 -c "
from cloud_backend.storage import get_default_storage
from cloud_backend.analytics import metrics
import numpy as np
print(metrics.eer(np.random.randn(200), np.random.randint(0,2,200)))
print(get_default_storage().list_sessions())
"
```
