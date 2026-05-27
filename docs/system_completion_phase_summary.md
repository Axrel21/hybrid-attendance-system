# System completion phase — change summary

Fourth pass on the `deployment-refactor` branch. Adds the cloud backend
infrastructure the project brief required without changing any
edge-runtime behavior. Pair with
[`STABILIZATION_ANALYSIS.md`](STABILIZATION_ANALYSIS.md),
[`TELEMETRY.md`](TELEMETRY.md),
[`REPOSITORY_LAYOUT.md`](REPOSITORY_LAYOUT.md),
[`refactor_change_summary.md`](refactor_change_summary.md), and
[`final_repo_separation_summary.md`](final_repo_separation_summary.md)
for the per-pass history.

## 1. Files modified

| Path | Change |
|------|--------|
| `shared/contracts.py` | Added telemetry / dashboard / WebSocket path constants, batching defaults, and `TELEMETRY_EVENT_TYPES` vocabulary. No existing constants changed. |
| `shared/schemas.py` | Added `SESSION_METADATA_FIELDS`, `TELEMETRY_EVENT_FIELDS`, `SESSION_SUMMARY_FIELDS`. |
| `shared/__init__.py` | Re-exports the new constants and tuples. |
| `deployment/cloud/CLOUD_BUNDLE.txt` | Adds `cloud_backend/` and its four subpackage directories (rsync `--files-from` doesn't recurse; subdirs must be listed explicitly). |
| `.gitignore` | Adds `cloud_storage/` (telemetry filesystem store) and `/dist/` (packaging output). |

No file moved. `edge/main.py`, `cloud/main.py`, `edge/cloud_client.py`,
`edge/offload_router.py`, `config/experiment_session.py`, the DIAG and
TELEMETRY CSV schemas, and the `/verify/image` multipart contract are
unchanged.

## 2. Files added

### Edge

| Path | Purpose |
|------|---------|
| `edge/telemetry_uploader.py` | Standalone post-session uploader (CLI + class). Reads an `experiments/exp_<id>/` directory and posts `/telemetry/sessions/start`, batched `/telemetry/ingest`, then `/telemetry/sessions/end` with a CSV-derived summary. The edge runtime never imports this module; the Pi keeps running offline regardless. |

### Cloud backend

| Path | Purpose |
|------|---------|
| `cloud_backend/__init__.py` | Package marker; documents the composite design rules. |
| `cloud_backend/server.py` | Composite FastAPI app. Imports `cloud/main.py`'s `app`, mutates it to include the telemetry + dashboard routers, registers the WS endpoint. Falls back to a placeholder if the verification stack isn't installed (dev-host scenarios) with a loud warning. Adds a diagnostic `GET /backend/info` route. |
| `cloud_backend/schemas.py` | Pydantic models (`SessionStartRequest`, `SessionEndRequest`, `TelemetryEvent`, `TelemetryBatch`, `IngestAck`, `SessionAck`, `SessionListResponse`, `SessionDetailResponse`, `ExperimentRow`, `ExperimentListResponse`, `MetricResponse`). |
| `cloud_backend/storage.py` | Filesystem-backed `TelemetryStorage` under `$CLOUD_STORAGE_DIR` (default `<repo>/cloud_storage/`). Atomic JSON writes for `metadata.json`/`summary.json`, append-only JSONL for `events.jsonl`, append-only JSONL `index.jsonl`. Thread-safe via a single `RLock`. Module-level `get_default_storage()` singleton. |
| `cloud_backend/telemetry/__init__.py` | Re-exports the ingestion router. |
| `cloud_backend/telemetry/api.py` | `POST /telemetry/sessions/start`, `POST /telemetry/sessions/end`, `POST /telemetry/ingest`, `GET /telemetry/healthz`. Broadcasts ingested events to the WS hub (best-effort; WS failures never break ingest). |
| `cloud_backend/dashboard/__init__.py` | Re-exports the read-side router. |
| `cloud_backend/dashboard/api.py` | `GET /api/sessions`, `/api/sessions/{id}`, `/api/sessions/{id}/telemetry`, `/api/sessions/{id}/summary`, `/api/experiments`, `/api/experiments/{label}`, `/api/metrics/{agreement,offload,latency}`. |
| `cloud_backend/dashboard/websocket.py` | `WS /ws/telemetry`. Bounded per-subscriber queue (256 frames) with oldest-drop on overflow. Optional `?session_id=` filter. Hello frame on accept, JSON `pong` on `ping`. |
| `cloud_backend/experiments/__init__.py` | Re-exports `ExperimentRegistry`. |
| `cloud_backend/experiments/registry.py` | Read projection: sessions grouped by `experiment_label`. Provides per-experiment summary including session count, time range, total events, and a lightweight attack-type breakdown from session metadata. |
| `cloud_backend/analytics/__init__.py` | Package marker. |
| `cloud_backend/analytics/metrics.py` | Pure-function helpers: `agreement_rate`, `offload_outcome_distribution`, `latency_summary`, `far_frr`, `roc_curve`, `eer`. Numpy only — pandas not imported. All return `{"n": 0, ...}` on empty input. |
| `cloud_backend/README.md` | Layout + storage + WS + edge uploader overview. |

### Deployment

| Path | Purpose |
|------|---------|
| `deployment/cloud/run_backend.sh` | Launcher for the composite backend. Sets `cwd=cloud/` (so `cloud/main.py`'s gallery path resolves) and `--app-dir <repo>` (so `cloud_backend` is importable). Defaults to `--host 0.0.0.0 --port 8000`; flags pass through to uvicorn. |

### Docs

| Path | Purpose |
|------|---------|
| `docs/system_completion_phase_summary.md` | This file. |

## 3. APIs added

All new endpoints are served only when the composite is run via
`deployment/cloud/run_backend.sh` (or any uvicorn pointing at
`cloud_backend.server:app`). Running the legacy `uvicorn cloud.main:app`
exposes only the existing verification surface.

### Telemetry ingestion (edge → cloud)

| Method | Path | Pydantic | Purpose |
|--------|------|----------|---------|
| POST | `/telemetry/sessions/start` | `SessionStartRequest` → `SessionAck` | Register a new session; write `metadata.json`. |
| POST | `/telemetry/sessions/end` | `SessionEndRequest` → `SessionAck` | Mark session ended; write `summary.json`. |
| POST | `/telemetry/ingest` | `TelemetryBatch` → `IngestAck` | Append a batch of events; broadcast to WS subscribers. |
| GET | `/telemetry/healthz` | dict | Storage reachability probe. |

### Dashboard reads (cloud → dashboard)

| Method | Path | Pydantic | Purpose |
|--------|------|----------|---------|
| GET | `/api/sessions?limit=&offset=&experiment_label=` | `SessionListResponse` | Paginated session list. |
| GET | `/api/sessions/{id}` | `SessionDetailResponse` | Metadata + summary + event count. |
| GET | `/api/sessions/{id}/telemetry?limit=&offset=` | dict | Paginated event stream. |
| GET | `/api/sessions/{id}/summary` | dict | Summary only (lighter payload). |
| GET | `/api/experiments` | `ExperimentListResponse` | Sessions grouped by `experiment_label`. |
| GET | `/api/experiments/{label}` | dict | Per-experiment summary + session id list. |
| GET | `/api/metrics/agreement?session_id=&experiment_label=` | `MetricResponse` | Edge/cloud agreement rate. |
| GET | `/api/metrics/offload?session_id=&experiment_label=` | `MetricResponse` | Offload-outcome distribution. |
| GET | `/api/metrics/latency?session_id=&experiment_label=&key=` | `MetricResponse` | Percentile summary over a numeric event field (defaults to `cloud_rtt_ms`). |
| GET | `/backend/info` | dict | Diagnostic: which routes are mounted, storage root, WS subscriber count. |

### Live telemetry

| Path | Protocol | Notes |
|------|----------|-------|
| `/ws/telemetry?session_id=` | WebSocket | Receives `{"type":"hello",...}` on connect, then `{"type":"telemetry_batch","session_id":..., "events":[...]}` per ingest. Accepts `"ping"` text messages and replies with `{"type":"pong"}`. |

## 4. Telemetry flows added

```
   ┌───────────────────────────────────────┐
   │ Raspberry Pi — edge runtime           │
   │                                        │
   │  edge/main.py  ────►  experiments/    │
   │                       exp_<id>/...    │  (CSVs, durable buffer)
   └─────────┬─────────────────────────────┘
             │ (offline-resilient)
             │
             │ python -m edge.telemetry_uploader \
             │     --session experiments/exp_<id>/ \
             │     --cloud http://cloud:8000
             │
             ▼
   ┌───────────────────────────────────────┐
   │ Cloud — cloud_backend.server          │
   │                                        │
   │  POST /telemetry/sessions/start  ──►  │
   │  POST /telemetry/ingest         ──►   │  cloud_storage/sessions/<id>/
   │  POST /telemetry/sessions/end   ──►   │      metadata.json
   │                                        │      events.jsonl
   │                                        │      summary.json
   │  ws://.../ws/telemetry  ◄── fanout    │
   │                                        │
   │  GET /api/sessions               ◄──  │  dashboard reads
   │  GET /api/metrics/*              ◄──  │  analytics consumers
   └───────────────────────────────────────┘
```

The verification flow (`POST /verify/image`) is unchanged and runs in
parallel with telemetry ingestion on the same FastAPI app.

## 5. Infrastructure added

- **Filesystem store** (`cloud_backend/storage.py`). No DB. JSON +
  JSONL on disk. Atomic writes. Rotating is `rm -rf $CLOUD_STORAGE_DIR`.
- **In-process WS hub** (`cloud_backend/dashboard/websocket.py`).
  Bounded per-subscriber queue. Backpressure drops oldest frame on the
  affected connection only; ingest is never blocked.
- **Composite app pattern** (`cloud_backend/server.py`). Loads the
  verification app and mutates its router list. Verification module is
  unchanged; running it standalone (`uvicorn cloud.main:app`) still
  works.
- **Edge-side post-session uploader** (`edge/telemetry_uploader.py`).
  Separate process; idempotent on metadata + summary, append-only on
  events. Dry-run mode for plan inspection. Replay mode for batch
  uploads of historical sessions.

## 6. Schema changes

- **Edge runtime CSV schemas** (`DIAG_COLUMNS`, `TELEMETRY_CSV_COLUMNS`,
  `attendance_log` header) — **unchanged**.
- **Cloud verification API** (`VerificationResponse`, `/verify/image`
  multipart contract) — **unchanged**.
- **New wire schemas**, all defined in `shared.contracts` /
  `shared.schemas` and codified by Pydantic in `cloud_backend.schemas`:
  - `SessionStartRequest` / `SessionEndRequest` / `SessionAck`
  - `TelemetryEvent` / `TelemetryBatch` / `IngestAck`
  - `SessionListResponse` / `SessionDetailResponse` / `MetricResponse`
  - `ExperimentRow` / `ExperimentListResponse`
- **Event-type vocabulary** (`TELEMETRY_EVENT_TYPES`):
  `session_start`, `session_end`, `frame_telemetry`, `diagnostic`,
  `attendance`, `offload`, `report`.

## 7. Deployment implications

- The Pi bundle is unchanged. `edge/telemetry_uploader.py` already
  ships under `edge/` so `PI_BUNDLE.txt` needs no edit.
- The cloud bundle now also ships `cloud_backend/` plus its four
  subpackages. `CLOUD_BUNDLE.txt` lists them explicitly (rsync
  `--files-from` doesn't recurse into listed directories).
- Two valid uvicorn entry points on the server:
  - `cd cloud && uvicorn main:app` — verification only (legacy).
  - `bash deployment/cloud/run_backend.sh` — composite (verification +
    telemetry + dashboard + WS).
- `cloud_storage/` is the new on-disk telemetry root, default at
  `<repo>/cloud_storage/`. Override with `CLOUD_STORAGE_DIR` env var.
  Gitignored.
- `cloud/requirements.txt` is unchanged — `uvicorn[standard]==0.29.0`
  already bundles the `websockets` package, so the WS endpoint works
  without new dependencies. `fastapi==0.111.0` and `pydantic==2.7.1`
  cover the new schemas.

## 8. Validation performed

| Check | Result |
|-------|--------|
| `python3 -m compileall -q shared config edge cloud cloud_backend research experiments run.py preprocess_dataset.py analyze_*.py test_env.py` | exit 0 |
| `import shared; from shared import TELEMETRY_INGEST_PATH, …` (24 new names) | OK |
| `TelemetryStorage` round-trip: `record_session_start` → `append_events` → `record_session_end` → `list_sessions` → `get_session` in a `tempfile.TemporaryDirectory` | OK; `event_count=1`, `has_summary=True` |
| Analytics helpers on synthetic data: `agreement_rate`, `offload_outcome_distribution`, `latency_summary`, `far_frr`, `roc_curve`, `eer` | All produce expected numbers (e.g. `mean(latency)=30.0`, ROC has 401 thresholds for 400 samples, EER ≈ 0.175 on the seeded synthetic distribution); empty input returns `{"n": 0, ...}` |
| Edge uploader end-to-end dry-run against a synthetic `exp_<id>/` directory with CSV diagnostic log | `started=1, events=3, batches=2, ended=1, failures=0`; session start payload includes thresholds + camera_backend; summary correctly counts `frames_total=3, matched_total=1, spoof_total=1, offload_total=1, offload_success_total=1` |
| `python3 -m edge.telemetry_uploader --help` | OK; usage prints with all flags |
| `bash deployment/cloud/run_backend.sh --help` (no uvicorn installed) | Fails with the documented "uvicorn not on PATH" hint — correct error path |
| `bash deployment/common/verify_manifests.sh` | Both bundles verified; cloud_backend subpackages now resolve in the cloud bundle |
| `bash deployment/common/package_cloud.sh /tmp/_dist` | 36 KB tarball; contains all 9 `cloud_backend/*/<file>.py` subpackage files, `cloud/`, `shared/`, `requirements_cloud.txt`; no edge/research/datasets leakage |

Runtime end-to-end (camera → cloud → dashboard WS) was **not**
exercised — explicitly out of scope per the brief ("Not: live runtime
validation — the developer will test and validate later"). The
edge-side runtime (`edge/main.py`) has zero code changes and therefore
cannot regress.

## 9. Unresolved risks

- **`cloud_backend.server` mutates the verification app's router list at
  import time.** If something else imports `cloud.main:app` in the same
  process, it observes the mutated app. In practice this only matters
  for unit tests; production servers run one entrypoint per process.
- **Composite-only `/backend/info` route** lives on the verification app
  too when the composite is imported. If a deployment runs the
  composite, then later switches to bare verification by restarting
  with `cloud.main:app`, `/backend/info` disappears — that's the
  intended behavior, but worth noting.
- **WS hub is in-process.** Multiple uvicorn workers would each have
  their own subscriber set. Stay at `--workers 1` (already the
  recommendation in `cloud/README.md` because of the ArcFace ONNX
  session).
- **`event.fields` is schema-loose by design.** Dashboards consuming
  the JSONL must tolerate missing keys per event type. `shared.contracts.TELEMETRY_EVENT_TYPES`
  is informative, not authoritative.
- **Storage growth is unbounded.** `cloud_storage/sessions/<id>/events.jsonl`
  grows linearly with frames. No retention policy is enforced; rotate
  manually until a retention pass is added.

## 10. Deferred validation items

- Live edge → cloud telemetry round-trip on real hardware (developer
  will run; this branch is infrastructure only).
- WebSocket fan-out behavior under concurrent ingest + multiple
  subscribers (load test deferred).
- ROC / EER analytics validated only on synthetic numpy data; should be
  re-run on a real session once the uploader has produced ingest data.
- The optional edge-runtime hook that would let `edge/main.py`
  upload telemetry inline during a run is deliberately **not**
  implemented. The post-session CLI uploader covers the common case
  without touching the runtime; an inline hook would be a future,
  coordinated pass.
- Adopting `shared.contracts` constants inside `cloud/main.py` and
  `edge/cloud_client.py` for the wire strings (still hard-coded).
  Tracked from pass 3.
- Retention / pruning policy for `cloud_storage/`.
- Authentication on `/telemetry/*` and `/api/*`. Open by design for
  research scale; lock down before any non-LAN deployment.
- Cross-origin policy for dashboards served from a separate host.
- Optional CSV → JSONL converter that runs on the Pi at session end so
  the uploader can stream a single ready-to-ingest file rather than
  three CSVs.
