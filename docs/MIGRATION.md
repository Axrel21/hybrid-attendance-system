# Repository reorganization (migration notes)

This document summarizes a **non-destructive** layout change focused on deployment boundaries and research-tool isolation. **Runtime semantics** (telemetry schemas, hybrid offload API, experiment session paths) are unchanged.

## What moved

| Before | After |
|--------|--------|
| `analyze_orientation.py` (monolithic) | Implementation: `research/analysis/orientation.py` — root file is a **shim** |
| `analyze_pi_perf.py` | `research/analysis/pi_perf.py` + root shim |
| `analyze_diag.py` | `research/analysis/liveness_diag_print.py` + root shim |
| `analyze_results.py` | `research/analysis/attendance_latency_offload.py` + root shim |
| `preprocess_dataset.py` | `research/dataset_preprocess.py` + root shim |
| `test_env.py` | `research/tools/smoke_dev_env.py` + root shim |
| `experiments/run_orientation_experiment.py` | Implementation: `research/experiments/orientation_launcher.py` — `experiments/` entry remains for `python -m experiments.run_orientation_experiment` |
| `deployment/attendance.service`, `pi_setup.sh`, `OPENCV_GUI_*.md`, `validate_opencv_gui.py` | `deployment/pi/` |

## What did **not** move (import stability)

- `edge/*.py` — unchanged paths (no `edge/runtime/` split) to avoid mass import churn.
- `config/` — remains at repo root (see `shared/README.md`).
- `run.py` — primary edge entry point.
- `cloud/*.py` — server layout unchanged aside from `gallery.py` bugfix (indentation).
- Per-run outputs still under `experiments/exp_<timestamp>/` via `config/experiment_session.py`.

## Dependency files

- **Edge:** canonical `edge/requirements-edge.txt`; `requirements_pi.txt` → `-r edge/requirements-edge.txt`.
- **Cloud:** canonical `cloud/requirements.txt`; root `requirements_cloud.txt` → `-r cloud/requirements.txt`.

## Path reference updates

- OpenCV Pi doc and errors now cite `deployment/pi/OPENCV_GUI_RASPBERRY_PI.md`.
- `run.py` references `deployment/pi/attendance.service`.

## For integrators

- Old commands (`python analyze_orientation.py`, `python preprocess_dataset.py`, `-m experiments.run_orientation_experiment`) **keep working** from repo root.
- New style: `python -m research.analysis.orientation` etc.

See `docs/DEPLOYMENT.md` for what to copy to the Pi vs the server.

---

## Second-pass operational stabilization

Additive boundary clarification and deployment automation. Runtime
semantics, telemetry schemas, and offload API are unchanged. See
`docs/STABILIZATION_ANALYSIS.md` for the full analysis and
`docs/refactor_change_summary.md` for the actual diff.

### Additions

| Path | Purpose |
|------|---------|
| `deployment/pi/PI_BUNDLE.txt` | Source-of-truth manifest for Pi rsync (`--files-from`). |
| `deployment/pi/deploy_pi.sh` | Convenience wrapper. Defaults to `--dry-run`; pass `--apply` to copy. |
| `deployment/cloud/CLOUD_BUNDLE.txt` | Source-of-truth manifest for the ArcFace server. |
| `deployment/cloud/deploy_cloud.sh` | Same shape as `deploy_pi.sh`. |
| `deployment/cloud/README.md` | Server-side post-deploy runbook. |
| `data/README.md` | Documents the runtime/research split inside `data/`. |
| `enrollment/README.md` | Confirms `enrollment/` is dev-time-only and not part of the Pi bundle. |
| `docs/TELEMETRY.md` | Canonical emitter / schema / destination reference. |
| `docs/STABILIZATION_ANALYSIS.md` | This pass's operational analysis and risk inventory. |
| `experiments/index.jsonl` | Best-effort one-line-per-session index, appended by `config/experiment_session.py`. |

### Modifications

| Path | Change |
|------|--------|
| `requirments.txt` (typo) | Converted from a stale standalone pin set into a forwarder: `-r edge/requirements-edge.txt`. Matches `requirements_pi.txt`. |
| `config/experiment_session.py` | Added `_append_session_index`; called from `init_experiment_session` after the settings snapshot write. Try/except wrapped so an index failure can never abort the pipeline. |
| `README.md`, `docs/DEPLOYMENT.md`, `docs/VALIDATION.md` | Quick-link the new artifacts. |

### Intentionally not changed

- `edge/*` layout — first-pass rule preserved (no `edge/runtime/` split).
- `config/` location — still at repo root.
- `cloud/*` layout — bare imports + `cwd=cloud/` server contract unchanged.
- DIAG / telemetry CSV schemas — unchanged.
- `enrollment/` location — kept at root to preserve `python -m enrollment.enroll`.
- `shared/` — still a README-only doc pointer.

---

## Third-pass operational separation

Promotes `shared/` to a real (still dependency-light) Python package,
introduces `deployment/common/` for cross-cutting helpers, and ships
both bundles with `shared/`. Runtime code paths, telemetry schemas, and
the hybrid offload wire contract are unchanged.

### Additions

| Path | Purpose |
|------|---------|
| `shared/__init__.py` | Re-exports the stable contract names. |
| `shared/contracts.py` | HTTP endpoint paths, multipart and metadata field names, `VerificationResponse` field tuple, embedding-dim invariants (ArcFace 512, MobileFaceNet 128 / 192), defaults (`DEFAULT_JPEG_QUALITY`, `DEFAULT_TIMEOUT_S`, `DEFAULT_CLOUD_PORT`), `CONTRACT_VERSION`. |
| `shared/schemas.py` | Lazy `get_diag_columns()` / `get_telemetry_csv_columns()` plus verbatim `ATTENDANCE_CSV_COLUMNS` and `EXPERIMENT_INDEX_FIELDS`. |
| `deployment/common/README.md` | Describes the three helper scripts. |
| `deployment/common/verify_manifests.sh` | Dry-runs both bundles, fails fast if either is malformed or leaks paths. |
| `deployment/common/package_pi.sh` | Builds `dist/attendance_pi_<utc>.tar.gz`. |
| `deployment/common/package_cloud.sh` | Builds `dist/arcface_server_<utc>.tar.gz`. |
| `docs/REPOSITORY_LAYOUT.md` | Maps conceptual subsystems to current homes; documents what stays stable and what can safely separate next. |
| `docs/final_repo_separation_summary.md` | Implementation-focused diff record (see file for the full table). |

### Modifications

| Path | Change |
|------|--------|
| `shared/README.md` | Rewritten to describe the new contract modules. |
| `deployment/pi/PI_BUNDLE.txt` | Adds `shared/`. |
| `deployment/cloud/CLOUD_BUNDLE.txt` | Adds `shared/`. |
| `README.md` | Quick-link rows for `shared/`, `deployment/common/`, `docs/REPOSITORY_LAYOUT.md`. |

### Intentionally not changed (third pass)

- `edge/` and `cloud/` remain at their existing paths. The hypothetical
  `edge_runtime/` and `cloud_backend/` directories in the brief are
  intentionally **not** created — empty placeholder packages would
  add confusion without delivering function. Their conceptual mapping
  to today's code is documented in `docs/REPOSITORY_LAYOUT.md`.
- No runtime module was rewritten. `edge/cloud_client.py` and
  `cloud/main.py` still hard-code the contract strings; future passes
  can adopt the constants in `shared/contracts.py` once a coordinated
  cross-component update is desired.

---

## Fourth-pass system completion

Builds the cloud-side telemetry / dashboard / WebSocket infrastructure
and adds a post-session edge uploader. No edge-runtime code changes;
no schema changes to the existing `diagnostic_log.csv` /
`telemetry_log.csv` / `attendance_log.csv` / `VerificationResponse`
shapes. See `docs/system_completion_phase_summary.md` for the full diff
record.

### Additions

| Path | Purpose |
|------|---------|
| `cloud_backend/` (new package) | Composite cloud backend: `server.py`, `storage.py`, `schemas.py`, `telemetry/api.py`, `dashboard/api.py`, `dashboard/websocket.py`, `experiments/registry.py`, `analytics/metrics.py`. Mounts on top of `cloud/main.py`; verification unchanged. |
| `cloud_backend/README.md` | Layout, storage, WS, edge-uploader overview. |
| `deployment/cloud/run_backend.sh` | Launcher for the composite (verification + telemetry + dashboard + WS). |
| `edge/telemetry_uploader.py` | Standalone CLI that reads an `experiments/exp_<id>/` directory and posts `/telemetry/sessions/{start,end}` + batched `/telemetry/ingest`. Not imported by the runtime. |
| `docs/system_completion_phase_summary.md` | Concise change record for this pass. |

### Modifications

| Path | Change |
|------|--------|
| `shared/contracts.py` | Adds telemetry / dashboard / WS path constants, batching defaults, `TELEMETRY_EVENT_TYPES`. |
| `shared/schemas.py` | Adds `SESSION_METADATA_FIELDS`, `TELEMETRY_EVENT_FIELDS`, `SESSION_SUMMARY_FIELDS`. |
| `shared/__init__.py` | Re-exports the new constants. |
| `deployment/cloud/CLOUD_BUNDLE.txt` | Lists `cloud_backend/` + its four subpackages explicitly (rsync `--files-from` does not recurse). |
| `.gitignore` | Adds `cloud_storage/` and `/dist/`. |

### Intentionally not changed (fourth pass)

- `edge/main.py` and the offload path. The edge runtime keeps its
  current code; the uploader runs as a separate process.
- `cloud/main.py`. The composite mounts it; the verification module
  itself is untouched.
- Existing CSV schemas and `VerificationResponse` shape.
- No new dependencies in `cloud/requirements.txt`: `uvicorn[standard]`
  already provides `websockets`.

---

## Fifth-pass stabilization & experimentation hardening

Adds observability-before-optimization infrastructure: experiment
protocol sidecar, offline stabilization diagnostics, threshold-sweep
tooling, cloud-side stabilization / calibration analytics, and six new
dashboard read endpoints. Edge runtime, deployment topology, and CSV
schemas are unchanged. See
`docs/stabilization_infrastructure_phase_summary.md` for the full diff
record.

### Additions

| Path | Purpose |
|------|---------|
| `research/experiment_protocol.py` | `ExperimentProtocol` dataclass + CLI; writes `experiments/exp_<id>/config/experiment_protocol.json`. |
| `research/analysis/stabilization.py` | Eight-dimension offline summary from `diagnostic_log.csv`. |
| `research/analysis/threshold_sweep.py` | Threshold what-if + hysteresis flip-flop counter. |
| `cloud_backend/analytics/stabilization.py` | Cloud-side orientation / confidence / PAD / thermal / bbox metrics over the event stream. |
| `cloud_backend/analytics/calibration.py` | Cloud-side threshold sweep + hysteresis count + confidence distribution. |
| `docs/EXPERIMENT_PROTOCOL.md` | Schema + lifecycle + CLI examples for the protocol sidecar. |
| `docs/STABILIZATION_DIAGNOSTICS.md` | Offline + cloud diagnostic surface reference. |
| `docs/stabilization_infrastructure_phase_summary.md` | Concise change record for this pass. |

### Modifications

| Path | Change |
|------|--------|
| `shared/contracts.py` | Six metric path constants, two session-scoped path templates, `EXPERIMENT_PROTOCOL_VERSION`, controlled vocabularies (`ATTACK_TYPES`, `LIGHTING_LABELS`, `ORIENTATION_LABELS`, `MOUNTING_LABELS`, `MOVEMENT_LABELS`), `STABILIZATION_METRIC_KEYS`. |
| `shared/schemas.py` | `EXPERIMENT_PROTOCOL_FIELDS`, `SESSION_CATEGORY_FIELDS`. |
| `shared/__init__.py` | Re-exports the new names. |
| `cloud_backend/analytics/__init__.py` | Wires `stabilization` and `calibration` submodules into the public namespace. |
| `cloud_backend/schemas.py` | `SessionStartRequest.protocol: Dict | None` (optional, backward-compatible). |
| `cloud_backend/experiments/registry.py` | `categorize_session()`, `session_protocol()`, `session_category()`. |
| `cloud_backend/experiments/__init__.py` | Re-exports `categorize_session`. |
| `cloud_backend/dashboard/api.py` | Six metric endpoints + protocol / category routes. |
| `edge/telemetry_uploader.py` | `SessionPaths.experiment_protocol`; `build_session_start` reads the sidecar. |

### Intentionally not changed (fifth pass)

- All existing CSV schemas (`DIAG_COLUMNS`, `TELEMETRY_CSV_COLUMNS`,
  `attendance_log` header) — touching them would force schema rotation
  on every edge node.
- `edge/main.py`, `cloud/main.py`, the offload path, deployment
  manifests, and `cloud/requirements.txt`.
- No new dependencies. The offline analyzers reuse the pinned `pandas` /
  `numpy` on the edge; cloud analytics use `numpy` (already pinned).
