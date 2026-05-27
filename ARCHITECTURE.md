# D3 Architecture — Dual-Camera Hybrid Attendance

**Status:** Proposal (no implementation)  
**Scope:** Introduce a second camera for classroom surveillance and presence validation  
**Constraint:** The existing entry-camera recognition pipeline (D1/D2) is complete and frozen

---

## 1. Full D3 Architecture Proposal

### 1.1 Problem statement

D1 and D2 deliver identity-authenticated attendance from a single entry camera:

```
Entry Pi Camera
  → YuNet → alignment → MobileFaceNet → confidence router
      → high confidence: local recognition
      → low confidence: cloud ArcFace verification
  → RecognitionEvent → AttendanceEngine → Dashboard
```

D3 adds a **second camera inside the classroom** whose sole job is **presence validation**. It must never run face recognition, never emit `gallery_identity`, and never invoke MobileFaceNet or ArcFace.

The two cameras operate in parallel with distinct responsibilities:

| Camera | Location | Hardware | Purpose | Identity |
|--------|----------|----------|---------|----------|
| **Entry** | Outside classroom | Raspberry Pi 4 + Pi Camera | Authentication | Yes — existing pipeline |
| **Surveillance** | Inside classroom | Dedicated host + USB/IP camera | Presence validation | No — anonymous person detection only |

### 1.2 System context

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           OUTSIDE CLASSROOM                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  ENTRY NODE (unchanged — D1/D2)                                      │   │
│  │  run.py → FinalHybridEdge                                            │   │
│  │  YuNet → align → MobileFaceNet → router → [ArcFace cloud]            │   │
│  │  AttendanceIngestionClient → POST /attendance/recognition/events     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ RecognitionEvent (identity)
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CLOUD BACKEND (extended)                            │
│  ┌─────────────────────┐    ┌──────────────────────────────────────────┐  │
│  │ RecognitionIngestor │    │ PresenceIngestor (NEW)                     │  │
│  │ AttendanceEngine    │◄───│ PresenceCorrelator (NEW)                   │  │
│  │ (UNCHANGED)         │    │ PresenceEngine (NEW)                       │  │
│  └─────────────────────┘    │ OccupancyAggregator (NEW)                  │  │
│                              │ ExceptionDetector (NEW)                     │  │
│                              └──────────────────────────────────────────┘  │
│  PostgreSQL: attendance_* (unchanged) + presence_* + occupancy_* (NEW)   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ▲
                                      │ PresenceObservationEvent (anonymous)
                                      │ OccupancySnapshotEvent
                                      │ SurveillanceExceptionEvent (internal)
┌─────────────────────────────────────────────────────────────────────────────┐
│                           INSIDE CLASSROOM                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  SURVEILLANCE NODE (NEW — D3)                                        │   │
│  │  surveillance/run.py → SurveillanceRuntime                           │   │
│  │  Camera → PersonDetector → AnonymousTracker → ZoneEvaluator          │   │
│  │  OccupancyEstimator → PresenceIngestionClient                        │   │
│  │  POST /attendance/presence/observations                              │   │
│  │  POST /attendance/presence/occupancy                                 │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                              Dashboard (extended polling panels)
```

### 1.3 Design principles

1. **Pipeline isolation.** The entry recognition stack (`edge/`, `cloud/`, `AttendanceEngine`) is not modified. Surveillance is a new top-level package with its own runtime, models, and HTTP contracts.

2. **No cross-model identity on surveillance.** Person detection uses bounding-box geometry and motion only. There is no face embedding, no gallery lookup, and no offload to ArcFace.

3. **Backend-side correlation.** Entry authentication (`gallery_identity`) and anonymous surveillance tracks are linked only inside `cloud_backend/surveillance/correlator.py`, using temporal and spatial heuristics — not on the surveillance device.

4. **Parallel state tracks.** `AttendanceRecord.state` (undetected → confirmed) continues to be driven exclusively by `RecognitionEvent`s. A new `PresenceRecord` tracks classroom presence duration and exceptions without rewriting attendance transitions.

5. **Advisory-first exceptions.** D3 exceptions are logged, surfaced on the dashboard, and stored in `surveillance_exceptions`. They do not retroactively change `AttendanceEngine` thresholds or suppress recognition events unless a future milestone explicitly enables enforcement.

6. **Reuse D.2A camera registry.** Both cameras register in `camera_sources` with role metadata in `meta_json`. Resolution logic extends with a role filter; D.1 global fallback remains entry-only.

### 1.4 Surveillance runtime pipeline

Per frame, the surveillance node executes:

```
Camera capture (640×480 @ 15 fps default)
  → PersonDetector (e.g. lightweight ONNX person detector — NOT YuNet face)
  → AnonymousTracker (IoU / centroid association, local track IDs only)
  → ZoneEvaluator (entry threshold, seating ROI, exit threshold)
  → OccupancyEstimator (count active tracks per zone + room total)
  → EventEmitter (debounced zone crossings, periodic occupancy samples)
  → PresenceIngestionClient (HTTP POST, never raises)
  → Local CSV telemetry (append-only, separate schema from edge DIAG_COLUMNS)
```

**Explicitly excluded from surveillance:**

- YuNet, `align_face`, MobileFaceNet, TFLite gallery
- PAD / liveness engine
- `CloudVerificationClient`, `/verify/image`
- `AttendanceIngestionClient`, `/attendance/recognition/events`
- Any import of `edge.pipeline_controller`, `edge.offload_router`, or `data/known_faces.json`

### 1.5 Backend processing pipeline

When presence events arrive:

```
PresenceIngestor
  → resolve classroom via camera_id (D.2A registry, role=surveillance)
  → resolve active lecture in classroom
  → append to presence_observation_log (audit)
  → OccupancyAggregator.ingest_snapshot()   [if occupancy sample]
  → PresenceCorrelator.on_observation()     [if zone crossing]
       ↔ reads recent RecognitionEventLog rows for same classroom
       ↔ assigns tentative track ↔ identity links (confidence scored)
  → PresenceEngine.process()                [updates PresenceRecord]
  → ExceptionDetector.evaluate()            [may emit SurveillanceException]
```

Correlation heuristics (configurable, documented in correlator):

| Signal | Weight | Description |
|--------|--------|-------------|
| Temporal proximity | High | Entry `RecognitionEvent` within `ENTRY_PRESENCE_WINDOW_S` (default 120 s) of entry-zone `in` crossing |
| Sequence | Medium | Entry-zone `in` before seating-zone presence |
| Count balance | Medium | Number of entry crossings ≤ number of authenticated identities in window |
| Departure symmetry | Low | Exit-zone `out` correlates with end of seating presence |

Correlation produces a **link score** (0.0–1.0), not a second identity claim. Links below `MIN_LINK_SCORE` remain anonymous.

### 1.6 Occupancy estimation

Occupancy is computed at two levels:

**Instantaneous (per frame / debounced sample):**

- `estimated_count`: active anonymous tracks with centroid inside seating ROI
- `zone_counts`: map of zone_id → count
- Emitted as `OccupancySnapshotEvent` every `OCCUPANCY_SAMPLE_INTERVAL_S` (default 30 s)

**Session aggregates (backend, per lecture):**

- `peak_occupancy`, `mean_occupancy`, `enrolled_count`
- `unlinked_track_count`: tracks never correlated to an authenticated identity
- Stored in `occupancy_sessions` table, refreshed on each snapshot

### 1.7 Entry confirmation

"Entry confirmation" is the backend assertion that an authenticated identity has a matching anonymous entry-zone crossing:

```
RecognitionEvent accepted (AttendanceEngine hit)
        +
PresenceObservationEvent (zone=entry, direction=in) within window
        +
PresenceCorrelator link_score ≥ MIN_LINK_SCORE
        ⇒ PresenceRecord: awaiting_presence → entry_confirmed
```

If authentication occurs but no entry-zone crossing arrives within the window, `ExceptionDetector` emits `entry_without_presence`.

### 1.8 Presence duration accumulation

Once `entry_confirmed`, the backend accumulates **seated presence seconds**:

- Increment while correlated track centroid remains in seating ROI
- Pause when track is lost (grace period `TRACK_LOST_GRACE_S`, default 15 s)
- Finalize segment on exit-zone `out` or track expiry
- Store running total on `PresenceRecord.presence_duration_s` and append segment rows to `presence_segments`

Duration is **independent** of `AttendanceRecord.confirmed_at` but displayed alongside it on the dashboard.

### 1.9 Exception generation

Exceptions are first-class records, not thrown errors:

| Type | Trigger | Default severity |
|------|---------|------------------|
| `entry_without_presence` | Auth accepted, no entry-zone crossing in window | warn |
| `presence_without_entry` | Entry-zone `in` with no recent auth | alert |
| `occupancy_over_capacity` | `estimated_count` > enrolled + tolerance | warn |
| `early_departure` | Seated presence ended before `active_window_closed` with duration < threshold | info |
| `prolonged_absence_after_auth` | Auth + entry confirmed, seating presence never started | warn |
| `surveillance_dropout` | No observations for > `SURVEILLANCE_HEARTBEAT_TIMEOUT_S` | alert |
| `track_orphan` | Anonymous track in seating > `ORPHAN_TRACK_THRESHOLD_S` without link | info |
| `count_mismatch` | Authenticated count vs entry crossings diverge | warn |

### 1.10 Dashboard extensions

The existing attendance ops UI (`/dashboard/attendance`) gains **additive panels** (no changes to recognition polling):

- Classroom occupancy sparkline (polls `/attendance/lectures/{id}/occupancy`)
- Per-student presence duration column (polls `/attendance/lectures/{id}/presence`)
- Exception feed (polls `/attendance/lectures/{id}/exceptions`)
- Camera health badge for surveillance source (last observation timestamp)

Research telemetry dashboard (`/api/sessions/*`) remains filesystem-backed and unchanged.

---

## 2. Exact Folders and Files to Create

### 2.1 Surveillance edge runtime (new top-level package)

```
surveillance/
├── __init__.py
├── README.md                          # Runtime overview, env vars, deployment notes
├── run.py                             # CLI entry: python -m surveillance.run
├── main.py                            # SurveillanceRuntime main loop
├── camera.py                          # Capture abstraction (V4L2 / libcamera)
├── detector.py                        # PersonDetector ONNX wrapper
├── tracker.py                         # AnonymousTracker (no identity fields)
├── zones.py                           # ROI definitions, point-in-polygon helpers
├── occupancy.py                       # OccupancyEstimator
├── emitter.py                         # Debounced event batching
├── presence_client.py                 # HTTP → /attendance/presence/*
├── telemetry.py                       # Local CSV writer (SURVEILLANCE_CSV_COLUMNS)
├── session.py                         # Experiment session dir under surveillance_runs/
└── requirements-surveillance.txt      # Lightweight deps (opencv, onnxruntime, requests)
```

**Models (gitignored, shipped separately):**

```
models/
└── person_detector.onnx               # NEW weight file (not yunet.onnx)
```

### 2.2 Surveillance configuration

```
config/
└── surveillance_settings.py           # SURVEILLANCE_* env tunables (mirrors settings.py pattern)
```

`config/settings.py` is **not** modified; surveillance reads its own module.

### 2.3 Shared contracts (additive only)

```
shared/
├── contracts.py                       # APPEND: presence HTTP paths, event type vocab
└── schemas.py                           # APPEND: get_surveillance_csv_columns() lazy accessor
```

No new imports of cv2/insightface in `shared/`.

### 2.4 Cloud backend — surveillance domain

```
cloud_backend/
├── surveillance/
│   ├── __init__.py
│   ├── ingestor.py                    # PresenceIngestor
│   ├── correlator.py                  # Entry ↔ track correlation
│   ├── presence_engine.py             # PresenceRecord state machine
│   ├── occupancy_aggregator.py        # Snapshot rollups
│   ├── exception_detector.py          # Rule engine
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── presence.py                # Pydantic wire models
│   │   ├── occupancy.py
│   │   └── exception.py
│   └── queries/
│       ├── __init__.py
│       └── visibility.py              # Read-side fetch helpers
├── api/
│   ├── presence.py                    # POST /attendance/presence/observations
│   └── presence_visibility.py         # GET occupancy, presence, exceptions
├── models/
│   ├── presence_record.py
│   ├── presence_event.py
│   ├── presence_segment.py
│   ├── presence_observation_log.py
│   ├── occupancy_snapshot.py
│   ├── occupancy_session.py
│   └── surveillance_exception.py
└── db/migrations/versions/
    └── 005_surveillance_presence.py   # Alembic migration
```

**Server wiring (minimal touch):**

```
cloud_backend/server.py                # ADD: mount presence routers (2 lines + imports)
```

### 2.5 Dashboard (additive static assets)

```
cloud_backend/dashboard/
├── presence_ui.py                       # Route: GET /dashboard/presence (optional standalone)
└── static/presence/
    ├── index.html                       # Or extend attendance/index.html via partial
    ├── app.js                           # Polls new visibility endpoints
    └── styles.css
```

Alternative (preferred for D3.3): extend `cloud_backend/dashboard/static/attendance/app.js` with new sections behind `PRESENCE_DASHBOARD_ENABLED` — file listed here as optional merge target, not a new file if inlined.

### 2.6 Deployment

```
deployment/
├── surveillance/
│   ├── SURVEILLANCE_BUNDLE.txt          # rsync manifest (surveillance/, config/, shared/, models/)
│   ├── deploy_surveillance.sh
│   ├── surveillance.service             # systemd unit
│   └── README.md
└── cloud/CLOUD_BUNDLE.txt               # APPEND lines for new cloud_backend/surveillance/* paths
```

### 2.7 Documentation

```
docs/
├── ARCHITECTURE.md                      # This document (relocated from root if desired)
├── SURVEILLANCE.md                      # Operator runbook (zones, calibration, troubleshooting)
└── MIGRATION.md                         # APPEND D3 migration section only
```

### 2.8 Research (optional, post-D3)

```
research/analysis/
└── presence_correlation_eval.py         # Offline correlator threshold tuning
```

---

## 3. Existing Files That Must Remain Untouched

The following paths are **frozen** for D3. Changes require explicit out-of-scope approval.

### 3.1 Entry recognition pipeline (complete)

| Path | Reason |
|------|--------|
| `edge/main.py` | FinalHybridEdge loop, DIAG_COLUMNS, attendance emission |
| `edge/camera.py` | Entry camera capture |
| `edge/tracker.py` | Face track association |
| `edge/liveness.py` | PAD engine |
| `edge/align.py` | Face alignment |
| `edge/pipeline_controller.py` | MobileFaceNet matching |
| `edge/offload_router.py` | Confidence router |
| `edge/cloud_client.py` | ArcFace offload client |
| `edge/attendance_client.py` | RecognitionEvent POST |
| `edge/telemetry.py` | Edge CSV schema |
| `edge/telemetry_uploader.py` | Post-session upload |
| `run.py` | Entry entrypoint |

### 3.2 Cloud verification (complete)

| Path | Reason |
|------|--------|
| `cloud/main.py` | `/verify/image` handler |
| `cloud/arcface_verifier.py` | ArcFace inference |
| `cloud/gallery.py` | 512-d gallery |
| `cloud/enroll_gallery.py` | Gallery management |

### 3.3 D1/D2 attendance orchestration (complete)

| Path | Reason |
|------|--------|
| `cloud_backend/attendance/engine.py` | AttendanceEngine thresholds and transitions |
| `cloud_backend/attendance/ingestor.py` | RecognitionIngestor pipeline |
| `cloud_backend/attendance/state_machine.py` | AttendanceState enum |
| `cloud_backend/attendance/schemas/recognition.py` | RecognitionEvent wire contract |
| `cloud_backend/api/recognition.py` | POST /attendance/recognition/events |
| `cloud_backend/classroom/resolver.py` | D.1 / D.2A resolution (extend-only via new callers, not edits to recognition path) |
| `cloud_backend/sessions/controller.py` | Lecture lifecycle |
| `cloud_backend/models/attendance_record.py` | AttendanceRecord ORM |
| `cloud_backend/models/attendance_event.py` | AttendanceEvent ORM |
| `cloud_backend/models/recognition_event_log.py` | Recognition audit log |
| `cloud_backend/db/migrations/versions/001_*.py` … `004_camera_sources.py` | Existing migrations immutable |

### 3.4 Cross-cutting invariants

| Path | Reason |
|------|--------|
| `data/known_faces.json` | MobileFaceNet gallery |
| `models/yunet.onnx`, `models/mobilefacenet.tflite` | Entry models |
| `shared/contracts.py` | Existing VERIFY_* and TELEMETRY_* entries (append-only) |
| `config/settings.py` | Entry runtime tunables |
| `deployment/pi/PI_BUNDLE.txt` | Entry Pi manifest |
| CSV column order in `edge/main.py` DIAG_COLUMNS | Append-only rotation contract |

### 3.5 Allowed minimal touches (not frozen)

| Path | Allowed change |
|------|----------------|
| `cloud_backend/server.py` | Mount new routers |
| `shared/contracts.py` | Append new constants at file end |
| `shared/schemas.py` | Append lazy accessor |
| `deployment/cloud/CLOUD_BUNDLE.txt` | List new backend paths |
| `cloud_backend/dashboard/static/attendance/*` | Additive UI sections |
| `docs/MIGRATION.md` | Append D3 section |

---

## 4. Event Contracts

All new contracts versioned under `PRESENCE_CONTRACT_VERSION = "1.0"` in `shared/contracts.py`. Optional fields are forward-compatible; renames require version bump.

### 4.1 RecognitionEvent (unchanged — entry camera only)

Existing contract at `POST /attendance/recognition/events`:

```json
{
  "gallery_identity": "string (required)",
  "confidence": 0.0,
  "timestamp_ms": 1234567890123,
  "source": "edge_runtime",
  "classroom_id": "uuid | null",
  "camera_id": "entry-cam-01 | null"
}
```

Response: `IngestionResult` (unchanged). Surveillance never sends this event.

### 4.2 PresenceObservationEvent (new — surveillance camera)

`POST /attendance/presence/observations`

```json
{
  "camera_id": "surv-classroom-a-01",
  "classroom_id": "uuid | null",
  "timestamp_ms": 1234567890123,
  "observation_type": "zone_crossing | track_lifecycle | heartbeat",
  "zone_id": "entry | seating | exit | null",
  "direction": "in | out | null",
  "track_id": "local-anonymous-id",
  "track_age_frames": 42,
  "bbox_norm": [0.12, 0.34, 0.08, 0.22],
  "detector_confidence": 0.87,
  "source": "surveillance_runtime"
}
```

**Field rules:**

- `gallery_identity` is **prohibited** — requests containing it are rejected with `400`.
- `track_id` is opaque to the backend; valid for one surveillance session.
- `bbox_norm` is optional `[cx, cy, w, h]` in 0..1 coordinates.
- `observation_type=heartbeat` carries no zone/direction; used for dropout detection.

**Response (`PresenceIngestionResult`):**

```json
{
  "accepted": true,
  "disposition": "stored | no_active_lecture | unknown_camera | unknown_classroom | rejected_identity_field | invalid_zone",
  "lecture_id": "uuid | null",
  "classroom_id": "uuid | null",
  "camera_id": "surv-classroom-a-01",
  "observation_id": "uuid | null",
  "detail": "string | null"
}
```

### 4.3 OccupancySnapshotEvent (new — surveillance camera)

`POST /attendance/presence/occupancy`

```json
{
  "camera_id": "surv-classroom-a-01",
  "classroom_id": "uuid | null",
  "timestamp_ms": 1234567890123,
  "estimated_count": 14,
  "zone_counts": {
    "entry": 0,
    "seating": 13,
    "exit": 1
  },
  "active_track_ids": ["t-001", "t-002"],
  "frame_id": 98765,
  "source": "surveillance_runtime"
}
```

**Response:** same shape as `PresenceIngestionResult` with `disposition` including `snapshot_stored`.

### 4.4 PresenceCorrelationEvent (backend-internal audit)

Written to `presence_events` and optionally exposed via visibility API. Not accepted from external clients.

```json
{
  "presence_record_id": "uuid",
  "lecture_id": "uuid",
  "gallery_identity": "student_key",
  "surveillance_track_id": "t-001",
  "correlation_type": "entry_linked | seating_linked | departure_linked | link_expired",
  "link_score": 0.82,
  "recognition_event_log_id": "uuid | null",
  "timestamp_ms": 1234567890123,
  "meta": {}
}
```

### 4.5 SurveillanceExceptionEvent (backend-generated)

Persisted in `surveillance_exceptions`; surfaced on dashboard.

```json
{
  "id": "uuid",
  "lecture_id": "uuid",
  "classroom_id": "uuid",
  "exception_type": "entry_without_presence",
  "severity": "info | warn | alert",
  "gallery_identity": "string | null",
  "surveillance_track_id": "string | null",
  "timestamp_ms": 1234567890123,
  "detail": "Authenticated at T+0s; no entry-zone crossing within 120s",
  "resolved": false,
  "meta": {}
}
```

### 4.6 Camera registry extension (D.2A meta_json convention)

No migration to `camera_sources` columns. Register roles in `meta_json`:

```json
{
  "role": "entry | surveillance",
  "zones": {
    "entry": [[0.0, 0.6], [0.3, 0.6], [0.3, 1.0], [0.0, 1.0]],
    "seating": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.55], [0.1, 0.55]],
    "exit": [[0.7, 0.6], [1.0, 0.6], [1.0, 1.0], [0.7, 1.0]]
  },
  "mounting": "ceiling_mount"
}
```

Entry cameras omit `zones` or leave empty; zone polygons for surveillance are mirrored in `surveillance/zones.py` config on device with backend copy for validation.

### 4.7 HTTP path registry (to append in shared/contracts.py)

| Constant | Path |
|----------|------|
| `PRESENCE_OBSERVATIONS_PATH` | `/attendance/presence/observations` |
| `PRESENCE_OCCUPANCY_PATH` | `/attendance/presence/occupancy` |
| `PRESENCE_LECTURE_RECORDS_PATH_TEMPLATE` | `/attendance/lectures/{lecture_id}/presence` |
| `PRESENCE_LECTURE_OCCUPANCY_PATH_TEMPLATE` | `/attendance/lectures/{lecture_id}/occupancy` |
| `PRESENCE_LECTURE_EXCEPTIONS_PATH_TEMPLATE` | `/attendance/lectures/{lecture_id}/exceptions` |
| `PRESENCE_OBSERVATION_LOG_PATH` | `/attendance/presence/logs` |

---

## 5. State Machine

D3 introduces a **parallel presence state machine** per `(lecture_id, student_id)`. It does not replace or merge into `AttendanceState`.

### 5.1 AttendanceState (unchanged — entry camera)

```
undetected → candidate → initialized → confirmed
```

Terminal: `absent`, `late_entry`, `tech_dropout`, `manual_override`

Driven by: `AttendanceEngine.process_recognition_event()`  
Authority: `cloud_backend/attendance/engine.py`  
**No changes in D3.**

### 5.2 PresenceState (new — surveillance + correlator)

```
                    ┌──────────────────────────────────────┐
                    │           awaiting_presence          │
                    │  (lecture started, no auth yet)      │
                    └───────────────┬──────────────────────┘
                                    │ RecognitionEvent accepted
                                    ▼
                    ┌──────────────────────────────────────┐
                    │           auth_recorded              │
                    │  (identity known, no surveillance)   │
                    └───────────────┬──────────────────────┘
                                    │ entry zone in + link_score ≥ threshold
                                    ▼
                    ┌──────────────────────────────────────┐
                    │           entry_confirmed            │
                    └───────────────┬──────────────────────┘
                                    │ seating zone presence started
                                    ▼
                    ┌──────────────────────────────────────┐
                    │              present                 │◄──┐
                    │  (duration accumulating)             │   │ track lost < grace
                    └───────────────┬──────────────────────┘   │
                                    │ exit / lost > grace        │
                                    ▼                            │
                    ┌──────────────────────────────────────┐   │
                    │           departed                   │───┘ (re-entry → present)
                    └───────────────┬──────────────────────┘
                                    │ lecture window closed
                                    ▼
                    ┌──────────────────────────────────────┐
                    │           finalized                  │
                    └──────────────────────────────────────┘

Exception overlay (any state): exception_flagged → exception_resolved
```

**Transition authority:** `PresenceEngine` in `cloud_backend/surveillance/presence_engine.py`

**Forward transitions:**

| From | To | Trigger |
|------|----|---------|
| `awaiting_presence` | `auth_recorded` | Correlator observes matching `RecognitionEvent` for enrolled student |
| `auth_recorded` | `entry_confirmed` | Entry-zone `in` correlated within window |
| `entry_confirmed` | `present` | Seating-zone presence starts for linked track |
| `present` | `departed` | Exit-zone `out` or track lost beyond grace |
| `departed` | `present` | Re-entry into seating with same linked track |
| `*` | `finalized` | Lecture status ≠ `active_window_open` |

**Non-transition updates (state unchanged):**

- `present_duration_s` incremented each aggregation tick
- `link_score` updated on new observations
- Exception flags attached without forcing state rollback

### 5.3 OccupancySession state (new — classroom level)

```
inactive → sampling → stalled → finalized
```

| State | Meaning |
|-------|---------|
| `inactive` | No active lecture in classroom |
| `sampling` | Receiving occupancy snapshots within heartbeat timeout |
| `stalled` | Heartbeat timeout exceeded; `surveillance_dropout` exception emitted |
| `finalized` | Lecture window closed; aggregates frozen |

Authority: `OccupancyAggregator`

### 5.4 Anonymous track lifecycle (surveillance edge, ephemeral)

```
detected → active → lost → expired
```

Not persisted in PostgreSQL beyond observation log references. Track IDs recycle locally.

### 5.5 State interaction diagram

```
Entry Camera                         Surveillance Camera
     │                                       │
     │ RecognitionEvent                      │ PresenceObservationEvent
     ▼                                       ▼
AttendanceEngine                    PresenceIngestor
     │                                       │
     │ AttendanceRecord.state                │ OccupancyAggregator
     │ (candidate…confirmed)                 │ PresenceCorrelator ──► PresenceEngine
     │                                       │         │
     │                                       │         ▼
     │                                       │   PresenceRecord.state
     │                                       │   (auth_recorded…present)
     │                                       │
     └────────────── Dashboard ──────────────┘
              (shows both columns independently)
```

Attendance confirmation and presence finalization may occur in either order; neither blocks the other.

---

## 6. Telemetry Additions

### 6.1 Design rules

- Surveillance telemetry is **schema-isolated** from edge `DIAG_COLUMNS` / `TELEMETRY_CSV_COLUMNS`.
- New columns are **append-only** within surveillance schemas.
- Research filesystem telemetry (`cloud_storage/sessions/*`) accepts new event types without breaking existing dashboards.

### 6.2 Surveillance local CSV (`surveillance/telemetry.py`)

File: `surveillance_runs/run_<timestamp>/diagnostics/presence_log.csv`

**`SURVEILLANCE_CSV_COLUMNS` (initial):**

| Column | Type | Description |
|--------|------|-------------|
| `timestamp_ms` | int | Wall clock |
| `frame_id` | int | Monotonic frame counter |
| `track_id` | str | Anonymous local ID |
| `observation_type` | str | `detection \| zone_crossing \| occupancy \| heartbeat` |
| `zone_id` | str | `entry \| seating \| exit \| none` |
| `direction` | str | `in \| out \| none` |
| `bbox_cx` | float | Normalized centroid X |
| `bbox_cy` | float | Normalized centroid Y |
| `bbox_w` | float | Normalized width |
| `bbox_h` | float | Normalized height |
| `detector_confidence` | float | Person detector score |
| `occupancy_total` | int | Room estimate at sample time |
| `presence_sent` | int | 0/1 HTTP POST attempted |
| `presence_disposition` | str | Backend response disposition |
| `presence_rtt_ms` | float | Round-trip latency |
| `experiment_label` | str | Optional run tag |

### 6.3 Extended TELEMETRY_EVENT_TYPES (shared/contracts.py append)

```python
# Existing types unchanged; append:
"presence_observation",      # mirrors presence_log.csv rows
"occupancy_snapshot",        # periodic room counts
"surveillance_exception",    # backend-generated, uploaded if edge uploader extended
"presence_correlation",      # backend audit exported for research replay
"surveillance_session_start",
"surveillance_session_end",
```

### 6.4 New quality tags (shared/contracts.py append)

| Tag | Signal |
|-----|--------|
| `surveillance_dropout` | Heartbeat gap > threshold |
| `occupancy_over_capacity` | Peak count vs enrolled |
| `high_orphan_tracks` | Unlinked seating tracks fraction |
| `entry_presence_mismatch` | Auth count vs entry crossings divergence |
| `unstable_surveillance_feed` | FPS or detector latency variance |

### 6.5 New stabilization-style metric keys (optional dashboard endpoint)

`GET /api/metrics/surveillance` (future):

- `occupancy_mean`, `occupancy_peak`, `orphan_track_rate`
- `entry_correlation_rate` (linked auths / total auths)
- `presence_duration_p50_s`, `presence_duration_p95_s`
- `exception_rate_by_type`
- `surveillance_observation_latency_p95_ms`

### 6.6 PostgreSQL audit tables (telemetry at rest)

| Table | Purpose |
|-------|---------|
| `presence_observation_log` | Raw inbound observations (all dispositions) |
| `occupancy_snapshots` | Time-series occupancy |
| `presence_events` | Correlation + state transition audit |
| `surveillance_exceptions` | Exception feed |

Retention: configurable; default co-terminous with lecture finalize + 90 days.

### 6.7 Environment variables (surveillance node)

| Variable | Default | Description |
|----------|---------|-------------|
| `SURVEILLANCE_API_ENABLED` | `0` | Master switch for HTTP POST |
| `SURVEILLANCE_API_URL` | derived | Base URL for presence endpoints |
| `SURVEILLANCE_CAMERA_ID` | required | Matches `camera_sources.camera_id` |
| `SURVEILLANCE_CLASSROOM_ID` | optional | Override; else resolved by backend |
| `OCCUPANCY_SAMPLE_INTERVAL_S` | `30` | Snapshot period |
| `PRESENCE_INGEST_COOLDOWN_S` | `2` | Min gap between identical track events |
| `PERSON_DETECTOR_MODEL` | `models/person_detector.onnx` | Local ONNX path |
| `SURVEILLANCE_HEARTBEAT_INTERVAL_S` | `60` | Heartbeat observation period |

Backend correlator tunables live in `cloud_backend/surveillance/config.py` (not env on Pi).

---

## 7. Migration Strategy

### 7.1 Guiding constraints

- Zero downtime for entry-only deployments: surveillance features gated behind flags.
- No retroactive migration of `attendance_records` or `recognition_event_log`.
- Database changes are additive migration `005_surveillance_presence.py` only.
- Pi bundle unchanged; surveillance ships via new `SURVEILLANCE_BUNDLE.txt`.

### 7.2 Phase rollout

| Phase | Milestone | Deliverables | Entry pipeline | Backend flags |
|-------|-----------|--------------|----------------|---------------|
| **D3.0** | Schema + stubs | Migration 005, empty routers return 501, contracts appended | Untouched | `PRESENCE_API_ENABLED=0` |
| **D3.1** | Ingest storage | PresenceIngestor stores observations + occupancy; no correlator | Untouched | `PRESENCE_API_ENABLED=1`, `PRESENCE_CORRELATOR_ENABLED=0` |
| **D3.2** | Edge runtime alpha | `surveillance/` package on classroom device; POST observations | Untouched | Correlator off; validate CSV + logs |
| **D3.3** | Correlation | PresenceCorrelator + PresenceEngine active; presence_events populated | Untouched | `PRESENCE_CORRELATOR_ENABLED=1` |
| **D3.4** | Exceptions | ExceptionDetector + dashboard panels | Untouched | `PRESENCE_EXCEPTIONS_ENABLED=1` |
| **D3.5** | Production | systemd unit, SURVEILLANCE_BUNDLE deploy, operator runbook | Untouched | All flags on per classroom |

### 7.3 Database migration (005)

**Create:**

- `presence_records` (FK: lecture_id, student_id; unique pair)
- `presence_events` (append-only audit)
- `presence_segments` (duration chunks)
- `presence_observation_log` (inbound wire audit)
- `occupancy_snapshots` (time series)
- `occupancy_sessions` (per-lecture aggregates)
- `surveillance_exceptions`

**Seed:**

- Extend `camera_sources.meta_json` for surveillance cameras via admin script (no ALTER on 004 tables).

**Do not:**

- Alter `attendance_records`, `attendance_events`, `recognition_event_log` columns
- Modify migrations 001–004

### 7.4 Deployment migration

1. Deploy backend with D3.0 flags off → run Alembic upgrade → verify existing attendance flow.
2. Register surveillance camera in `camera_sources` with `role=surveillance`.
3. Calibrate zones on device; store polygons in meta_json + local config.
4. Deploy surveillance bundle to classroom host (`deploy_surveillance.sh --dry-run` then `--apply`).
5. Enable `SURVEILLANCE_API_ENABLED=1` on device; confirm observations in `presence_observation_log`.
6. Enable correlator on backend; monitor `entry_correlation_rate` metric for one lecture cycle.
7. Enable dashboard panels; train operators on exception feed.

### 7.5 Rollback plan

| Layer | Rollback action |
|-------|-----------------|
| Surveillance device | Stop systemd unit; set `SURVEILLANCE_API_ENABLED=0` |
| Backend | Set `PRESENCE_API_ENABLED=0`; routers return 503 |
| Database | Do not downgrade 005 in production; tables remain inert |
| Entry pipeline | Never deployed as part of D3 — no rollback needed |

### 7.6 Compatibility matrix

| Configuration | Behavior |
|---------------|----------|
| Entry only (pre-D3) | Unchanged |
| Entry + backend D3 flags off | Unchanged |
| Entry + surveillance ingest, correlator off | Observations stored; attendance unchanged; no presence states |
| Full D3 | Dual-track attendance + presence on dashboard |

### 7.7 Testing gates (per phase)

- **D3.1:** POST malformed observation with `gallery_identity` → expect `400 rejected_identity_field`
- **D3.2:** Surveillance runtime runs 10 min without importing `edge.*`
- **D3.3:** Simulated auth + entry crossing → `PresenceRecord` reaches `entry_confirmed`
- **D3.4:** Auth without crossing → `entry_without_presence` exception row
- **D3.5:** Full lecture cycle; compare enrolled count vs peak occupancy; entry pipeline regression suite green

### 7.8 Documentation updates (post-implementation)

Append to `docs/MIGRATION.md` a "D3 dual-camera surveillance" section mirroring phase table above. Add `docs/SURVEILLANCE.md` operator guide. Update `docs/DEPLOYMENT.md` with third bundle reference.

---

## Appendix A — Responsibility matrix

| Concern | Entry (D1/D2) | Surveillance (D3) | Backend |
|---------|---------------|-------------------|---------|
| Identity authentication | Yes | **No** | Stores RecognitionEvent |
| Liveness / PAD | Yes | No | — |
| Cloud ArcFace offload | Yes | **No** | `/verify/image` unchanged |
| Person detection | Face (YuNet) | Body (person ONNX) | — |
| Occupancy counting | No | Yes | Aggregates snapshots |
| Entry confirmation | Emits auth | Emits zone crossing | Correlator links |
| Presence duration | No | Emits seating presence | Accumulates segments |
| Attendance state | Drives | **Does not drive** | AttendanceEngine |
| Presence state | No | Provides signals | PresenceEngine |
| Exceptions | No | Heartbeat / dropout | Rule engine |

## Appendix B — Glossary

| Term | Definition |
|------|------------|
| D.1 | Global lecture fallback when camera_id / classroom_id omitted |
| D.2A | Classroom-scoped resolution via camera_sources registry |
| D.2B | Edge AttendanceIngestionClient + attendance dashboard |
| D.3 | Dual-camera architecture (this document) |
| Anonymous track | Surveillance-local person blob with no identity |
| Link score | Backend confidence that a track corresponds to an authenticated student |
| RecognitionEvent | Identity-bearing ingest contract (entry only) |
| PresenceObservationEvent | Anonymous surveillance ingest contract |

---

*End of D3 architecture proposal.*
