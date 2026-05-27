# Repository layout — current vs conceptual

Single page mapping the current source tree to the operational
subsystems described in the project brief. Use this as the roadmap when
adding new code; consult
[`STABILIZATION_ANALYSIS.md`](STABILIZATION_ANALYSIS.md) for the
runtime/dependency analysis and
[`MIGRATION.md`](MIGRATION.md) for the per-pass history.

This repository is a **development source tree**, not the deployment
topology. The Pi and the cloud server receive disjoint subsets via
`deployment/pi/PI_BUNDLE.txt` and `deployment/cloud/CLOUD_BUNDLE.txt`.

---

## Conceptual subsystems → current homes

| Conceptual subsystem | Brief expectation | Current home in this repo |
|---|---|---|
| **Edge runtime** | YuNet, MobileFaceNet, tracker, PAD, orientation, hybrid offload client, frame telemetry, MJPEG, debug capture | `edge/` (operational package) + `run.py` (entry point) |
| **Cloud verification** | FastAPI server, ArcFace, gallery, image-only `/verify/image` | `cloud/` (server runs from `cwd=cloud/`) |
| **Shared contracts** | HTTP wire format, CSV schemas, embedding-dim invariants | `shared/` (lightweight; no cv2/insightface) |
| **In-process runtime config** | Tunables for the edge (thresholds, env-flag parsing, camera backend) | `config/settings.py`, `config/experiment_session.py`, `config/logging_setup.py` |
| **Per-run telemetry artefacts** | One isolated tree per pipeline run | `experiments/exp_<timestamp>/` (created by `config.experiment_session.init_experiment_session`) |
| **Cross-session experiment index** | Dashboard-readable enumeration of every run | `experiments/index.jsonl` (best-effort append from session init) |
| **Tagged orientation calibration log** | Legacy cross-session marker for orientation sessions | `data/experiment_sessions.jsonl` (orientation launcher only) |
| **Reports / per-run plots** | Auto-generated PNG / JSON / Markdown summaries | `edge/experiment_report.py` (lives with the runtime that emits the CSVs it consumes) |
| **Analytics / research scripts** | Offline notebooks and one-off plots | `research/analysis/*`, `research/experiments/*`, `research/tools/*` |
| **Enrollment (edge gallery)** | Build `data/known_faces.json` from `dataset_processed/<id>/*.webp` | `enrollment/` (dev-time only; not in the Pi bundle) |
| **Enrollment (cloud gallery)** | Build `cloud/gallery/<id>.npy` ArcFace gallery | `cloud/enroll_gallery.py` |
| **Pi deployment assets** | systemd unit, setup script, GUI notes, manifest, deploy script | `deployment/pi/` |
| **Cloud deployment assets** | Cloud-side manifest, deploy script, post-deploy runbook | `deployment/cloud/` |
| **Cross-cutting deployment tooling** | Manifest verifier, packaging tarballs | `deployment/common/` |

### Subsystems that are conceptually cloud-side but not yet runtime code

The project brief lists `cloud_backend/{telemetry, dashboard, analytics,
experiments, reports, verification}`. Of these:

| Subdir in brief | Status | Current home |
|---|---|---|
| `verification/` | **Live** | `cloud/main.py` + `cloud/arcface_verifier.py` + `cloud/gallery.py` |
| `telemetry/` | Not built — cloud only logs to stdout | Future: a small aggregator that ingests `experiments/index.jsonl` and the per-run CSVs |
| `dashboard/` | Not built | Future: a service consuming the aggregator above |
| `analytics/` | Lives in `research/analysis/` for now; runs offline against any session CSV | Future: optional cloud-hosted notebook / API layer |
| `experiments/` (aggregation, not the runtime output dir) | Not built | Future: registry on top of `experiments/index.jsonl` |
| `reports/` (cross-run) | Per-run reports are handled by `edge.experiment_report` on device | Future: cross-run aggregation can live cloud-side |

A `cloud_backend/` directory is intentionally **not** created in this
pass — it would otherwise sit empty and confuse the runtime tree. When
those services are built, they should be added under
`cloud_backend/<subsystem>/` and the cloud bundle manifest updated to
ship them alongside `cloud/`.

---

## Deployment topology

```
                ┌─────────────────────────────────────────────────────┐
                │   dev / CI / source machine                         │
                │                                                     │
                │   deployment/common/verify_manifests.sh             │
                │   deployment/common/package_{pi,cloud}.sh           │
                │                                                     │
                └────────────────────┬────────────────────────────────┘
                                     │
                ┌────────────────────┴────────────────────────────────┐
                ▼                                                     ▼
        ┌───────────────────────┐                       ┌────────────────────────────┐
        │ Raspberry Pi          │                       │ Cloud / server             │
        │                       │                       │                            │
        │ run.py                │                       │ cloud/                     │
        │ edge/                 │  POST /verify/image   │ ├── main.py (FastAPI)      │
        │ config/               │  multipart JPEG       │ ├── arcface_verifier.py    │
        │ shared/               │  (no embeddings)      │ ├── gallery.py             │
        │ deployment/pi/        │ ────────────────────▶ │ ├── enroll_gallery.py      │
        │ data/known_faces.json │                       │ └── gallery/<id>.npy       │
        │ models/yunet.onnx     │                       │ shared/                    │
        │ models/mobilefacenet… │                       │                            │
        │                       │                       │ (future cloud_backend/*)   │
        │ experiments/exp_…/    │                       │                            │
        │ experiments/index.…   │                       │                            │
        └───────────────────────┘                       └────────────────────────────┘
        (PI_BUNDLE.txt)                                 (CLOUD_BUNDLE.txt)

```

`shared/` ships on **both** sides so any future telemetry-aggregation
service on the cloud host can reference the same HTTP and CSV contracts
the edge uses today, without re-stating field names.

---

## What MUST stay stable

These elements are load-bearing for current operation and are
deliberately not moved or renamed:

- `edge/` package path — every consumer does `from edge.X import Y`.
- `cloud/` server directory — `uvicorn` runs from `cwd=cloud/`, bare
  imports (`from gallery import …`) depend on it.
- `config/settings.py`, `config/experiment_session.py`,
  `config/logging_setup.py` — referenced by edge, run.py, and research.
- `DIAG_COLUMNS`, `TELEMETRY_CSV_COLUMNS`, `attendance_log.csv`
  schemas — schema rotation in `edge/main.py` and `edge/telemetry.py`
  archives mismatched headers, so reordering / renaming is a hard break.
- `/verify/image` multipart contract — codified in
  `shared/contracts.py`; bump `CONTRACT_VERSION` on both sides if it
  ever changes.
- `data/known_faces.json` path — looked up via `_PROJECT_ROOT` in
  `edge/main.py`.

## What can safely separate next

If a future pass needs to push the conceptual layout further, the
lowest-risk next moves are:

1. Promote `enrollment/` to `research/enrollment/` after auditing
   external callers that hard-code `python -m enrollment.enroll`.
2. Introduce `cloud_backend/telemetry/` for the first real aggregation
   service, then update `CLOUD_BUNDLE.txt` to ship it.
3. Split `edge/` into `edge/runtime/` and `edge/instrumentation/` once
   every external script has been updated to the new import paths.

None of these are required for the current operational tree to work.
