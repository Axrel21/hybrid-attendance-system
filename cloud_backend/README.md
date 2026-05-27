# `cloud_backend/` — composite cloud backend

Extends the existing verification-only `cloud/` server with:

- **Telemetry ingestion** (`/telemetry/sessions/{start,end}`, `/telemetry/ingest`).
- **Read-side dashboard APIs** (`/api/sessions`, `/api/experiments`, `/api/metrics/{agreement,offload,latency}`).
- **Live telemetry WebSocket** (`/ws/telemetry`).
- **Analytics helpers** (ROC / FAR / FRR / EER, agreement, offload distribution, latency percentiles).

The verification flow in `cloud/main.py` is the authoritative offload
path; this package mounts on top of it. Run via:

```bash
bash deployment/cloud/run_backend.sh --host 0.0.0.0 --port 8000
```

The launcher sets `cwd=cloud/` (so `cloud/main.py`'s gallery path
resolves) and `--app-dir <repo>` (so the `cloud_backend` package is
importable).

## Layout

| Path | Role |
|------|------|
| `cloud_backend/server.py` | Composite FastAPI app — imports `cloud/main.py`'s `app`, mounts the new routers, registers the WS endpoint. Falls back to a placeholder app if the verification stack isn't installed (dev-host scenarios). |
| `cloud_backend/schemas.py` | Pydantic models for the API surface. |
| `cloud_backend/storage.py` | Filesystem-backed sessions + events store under `$CLOUD_STORAGE_DIR` (default `<repo>/cloud_storage/`). Atomic JSON writes, append-only JSONL events. No DB. |
| `cloud_backend/telemetry/api.py` | Ingestion router (edge → cloud). |
| `cloud_backend/dashboard/api.py` | Read-side dashboard router. |
| `cloud_backend/dashboard/websocket.py` | Live-telemetry WS hub. Bounded queue per subscriber. |
| `cloud_backend/experiments/registry.py` | Sessions grouped by `experiment_label`. |
| `cloud_backend/analytics/metrics.py` | Pure-function metric helpers (numpy only). |

## Storage layout

```
$CLOUD_STORAGE_DIR/
├── index.jsonl                     # one record per known session
└── sessions/
    └── <session_id>/
        ├── metadata.json           # from /telemetry/sessions/start
        ├── summary.json            # from /telemetry/sessions/end
        └── events.jsonl            # from /telemetry/ingest
```

`CLOUD_STORAGE_DIR` is gitignored at the repo level. Rotating the
storage is just `rm -rf` of the chosen directory.

## Edge-side uploader

The Pi never imports `cloud_backend`. It uses
`python -m edge.telemetry_uploader` as a separate process to push a
completed session directory to the cloud after the run. Failures in the
uploader cannot affect the runtime.

See `edge/telemetry_uploader.py` for the CLI surface (`--session`,
`--replay`, `--dry-run`, etc.).

## Wire-format contracts

All HTTP paths and JSON field tuples live in
[`shared/contracts.py`](../shared/contracts.py) and
[`shared/schemas.py`](../shared/schemas.py). Adding optional fields is
forward-compatible. Renaming or removing a field bumps
`CONTRACT_VERSION` in lockstep on both sides.

## What this package does NOT do

- Run any verification or recognition model. That stays in `cloud/`.
- Modify edge runtime behavior. The Pi keeps working offline.
- Mandate a database. JSON/JSONL on disk is the storage model.
- Compose a frontend. The dashboard APIs are groundwork; the UI is a
  separate future project.
