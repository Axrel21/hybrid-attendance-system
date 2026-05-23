# D3 Track 0.5 — MVP Plan

**Status:** Implementation roadmap (no code)  
**Parent doc:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (full D3 vision — deferred)  
**Track:** 0.5 — prove the second stream exists and the backend can receive it

---

## MVP goal

Demonstrate the minimum closed loop:

```
Student authenticates on Pi (unchanged D1/D2)
        ↓
Laptop webcam estimates classroom occupancy (scalar count)
        ↓
POST to temporary in-memory backend endpoint
        ↓
Backend logs: "presence observed"
```

No correlation between identity and occupancy. No persistence. No dashboard. No schema.

---

## A. Keep / Delete matrix

Audit of [`ARCHITECTURE.md`](ARCHITECTURE.md) against Track 0.5 constraints.

| ARCHITECTURE.md item | Verdict | MVP disposition |
|----------------------|---------|-----------------|
| Second camera (surveillance) concept | **Keep** | Laptop webcam, single stream |
| `surveillance/` top-level package | **Keep** | Reduced to 4 files |
| Pipeline isolation from `edge/*` | **Keep** | Hard rule |
| No face recognition on surveillance | **Keep** | Hard rule |
| Occupancy estimation (instantaneous count) | **Keep** | Scalar only; no history |
| `surveillance/run.py` | **Keep** | Main loop |
| `surveillance/camera.py` | **Keep** | Webcam capture |
| `surveillance/occupancy.py` | **Keep** | Fixed algorithm, no model picker |
| HTTP client to backend | **Keep** | As `surveillance/client.py` |
| PersonDetector ONNX + `models/person_detector.onnx` | **Delete** | No external model; fixed OpenCV-based approach |
| `surveillance/detector.py` | **Delete** | Fold minimal logic into `occupancy.py` |
| `surveillance/tracker.py` | **Delete** | Not needed for scalar occupancy |
| `surveillance/zones.py` | **Delete** | Single full-frame ROI |
| `surveillance/emitter.py` | **Delete** | Inline debounce in `run.py` |
| `surveillance/presence_client.py` | **Delete** | Renamed/consolidated → `client.py` |
| `surveillance/telemetry.py` | **Delete** | stdout + backend log sufficient |
| `surveillance/session.py` | **Delete** | No experiment dirs |
| `surveillance/requirements-surveillance.txt` | **Defer** | Document deps in README snippet inside plan; optional Track 1 |
| `config/surveillance_settings.py` | **Delete** | Env vars read inline in `run.py` |
| Multi-classroom / `camera_sources` registry | **Delete** | One hardcoded classroom label |
| Multiple surveillance streams | **Delete** | One webcam index |
| `PresenceIngestor` | **Delete** | In-memory handler only |
| `PresenceCorrelator` | **Delete** | Out of scope |
| `PresenceEngine` | **Delete** | Out of scope |
| `PresenceRecord` / `presence_records` table | **Delete** | Out of scope |
| `PresenceState` state machine | **Delete** | Out of scope |
| `OccupancyAggregator` + session aggregates | **Delete** | No history, no peak/mean |
| `occupancy_snapshots` / `occupancy_sessions` tables | **Delete** | Out of scope |
| `ExceptionDetector` + exception types | **Delete** | Out of scope |
| `surveillance_exceptions` table | **Delete** | Out of scope |
| Entry confirmation / link score | **Delete** | Out of scope |
| Presence duration accumulation | **Delete** | Out of scope |
| Alembic migration `005_surveillance_presence.py` | **Delete** | No DB changes |
| All new ORM models under `cloud_backend/models/presence_*` | **Delete** | Out of scope |
| `shared/contracts.py` presence path constants | **Delete** | Hardcode URL in `client.py` for MVP |
| `shared/schemas.py` surveillance CSV accessor | **Delete** | Out of scope |
| Dashboard extensions (`/dashboard/presence`, attendance UI) | **Delete** | Out of scope |
| `deployment/surveillance/` bundle + systemd | **Defer** | Manual `python -m surveillance.run` on laptop |
| `docs/SURVEILLANCE.md` operator runbook | **Defer** | Track 1 |
| `docs/MIGRATION.md` D3 section | **Defer** | Track 1 |
| Research / correlator eval scripts | **Delete** | Out of scope |
| Phased rollout D3.0–D3.5 | **Replace** | Single Track 0.5 checklist (Section C) |
| Extended `TELEMETRY_EVENT_TYPES` | **Delete** | Out of scope |
| New quality tags / metrics endpoints | **Delete** | Out of scope |
| `PresenceObservationEvent` wire contract | **Delete** | One minimal JSON body (Section E) |
| `OccupancySnapshotEvent` as separate endpoint | **Delete** | Single POST endpoint |
| `PresenceCorrelationEvent` | **Delete** | Out of scope |
| `SurveillanceExceptionEvent` | **Delete** | Out of scope |
| RecognitionEvent / AttendanceEngine | **Keep untouched** | Frozen |
| `edge/*`, `run.py`, ArcFace, recognition contracts | **Keep untouched** | Frozen |

---

## B. Minimal folder tree

```
hybrid-attendance-system/
├── edge/                          # UNTOUCHED
├── run.py                         # UNTOUCHED
├── cloud/                         # UNTOUCHED (ArcFace)
├── cloud_backend/
│   ├── attendance/                # UNTOUCHED (engine, ingestor, schemas)
│   ├── api/recognition.py         # UNTOUCHED
│   ├── server.py                  # MINIMAL TOUCH: mount one MVP router
│   └── surveillance_mvp/          # NEW — Track 0.5 only
│       ├── __init__.py
│       ├── store.py               # In-memory ring buffer
│       └── api.py                 # POST handler + optional GET for manual debug
│
└── surveillance/                  # NEW — laptop runtime only
    ├── __init__.py
    ├── run.py
    ├── camera.py
    ├── occupancy.py
    └── client.py
```

**Not created in Track 0.5:** `cloud_backend/surveillance/`, migrations, models, dashboard assets, `shared/` edits, deployment manifests, ONNX weights.

---

## C. Implementation order

Execute in sequence. Each step is independently testable before the next.

| Step | Work | Verification |
|------|------|--------------|
| **0** | Confirm D1/D2 entry path still runs unchanged | Existing attendance flow green |
| **1** | `cloud_backend/surveillance_mvp/store.py` — in-memory list (cap ~100 entries), thread-safe append | Unit-less manual import test |
| **2** | `cloud_backend/surveillance_mvp/api.py` — `POST /mvp/presence/observed` accepts JSON, appends to store, logs `"presence observed"` to stdout/logger | `curl` POST returns 200; log line appears |
| **3** | Mount MVP router in `cloud_backend/server.py` (one import + `include_router`) | `GET /backend/info` or manual route check |
| **4** | `surveillance/camera.py` — open default laptop webcam (index 0), read frames | Display or print frame shape once |
| **5** | `surveillance/occupancy.py` — fixed algorithm returns `estimated_count: int` from latest frame | Print count while waving hand / people move |
| **6** | `surveillance/client.py` — POST `{ estimated_count, timestamp_ms }` to MVP endpoint; never raises | `curl` receiver shows entries |
| **7** | `surveillance/run.py` — loop: capture → occupancy → if count changed OR interval elapsed → client POST | End-to-end: move in frame → backend log |
| **8** | Manual integration: Pi auth + laptop surveillance concurrently | Two independent log streams; no coupling required |

**Track 0.5 exit criteria:**

- Pi recognition → attendance dashboard unchanged.
- Laptop running `python -m surveillance.run` posts occupancy samples.
- Backend stdout/log contains `"presence observed"` with `estimated_count`.
- Restarting backend clears in-memory store (expected).

---

## D. Files to create

### Surveillance (laptop)

| File | Responsibility |
|------|----------------|
| `surveillance/__init__.py` | Package marker |
| `surveillance/camera.py` | `WebcamCapture`: open/ read / release; default device 0; 640×480 @ ~10 fps |
| `surveillance/occupancy.py` | `estimate_occupancy(frame) -> int`: single fixed method (e.g. MOG2 foreground blob count or OpenCV HOG — **one implementation, no config switch**) |
| `surveillance/client.py` | `PresenceObservedClient.post(estimated_count, timestamp_ms)`: HTTP POST; failures logged, not raised |
| `surveillance/run.py` | CLI entry; poll loop; debounce POST on count change or `POST_INTERVAL_S` (default 5 s) |

### Cloud (temporary)

| File | Responsibility |
|------|----------------|
| `cloud_backend/surveillance_mvp/__init__.py` | Package marker |
| `cloud_backend/surveillance_mvp/store.py` | Module-level in-memory store; `append(entry)`, `recent(n)`, `clear()` |
| `cloud_backend/surveillance_mvp/api.py` | FastAPI router: `POST /mvp/presence/observed`; optional `GET /mvp/presence/recent` for manual debug only |

### Minimal edit (not create)

| File | Change |
|------|--------|
| `cloud_backend/server.py` | `include_router(surveillance_mvp_router)` — no other edits |

**Total new files: 8.** **Total edited files: 1.**

---

## E. Exact boundaries

### E.1 Frozen — do not modify

```
edge/**
run.py
cloud/**
cloud_backend/attendance/**
cloud_backend/api/recognition.py
cloud_backend/classroom/resolver.py
cloud_backend/sessions/**
cloud_backend/models/**          # all existing ORM
cloud_backend/db/**              # migrations, alembic
cloud_backend/dashboard/**       # attendance UI
shared/**
config/**
deployment/**
data/known_faces.json
models/yunet.onnx
models/mobilefacenet.tflite
```

AttendanceEngine, RecognitionEvent schema, AttendanceIngestionClient, ArcFace `/verify/image` — all frozen.

### E.2 MVP surveillance runtime — in scope

| Allowed | Forbidden |
|---------|-----------|
| Read laptop webcam | Import `edge.*` |
| Compute one integer occupancy count | Face detection, YuNet, MobileFaceNet |
| POST JSON to MVP endpoint | POST `/attendance/recognition/events` |
| Env: `SURVEILLANCE_API_URL`, `WEBCAM_INDEX`, `POST_INTERVAL_S` | Gallery, ArcFace, PAD |
| stdout logging on laptop | CSV telemetry files |
| Single classroom implicit (no ID required in MVP) | `camera_sources` DB lookups |
| Single stream (webcam index 0 default) | Multi-camera routing |

### E.3 MVP backend — in scope

| Allowed | Forbidden |
|---------|-----------|
| In-memory list of observations | PostgreSQL reads/writes |
| Log line: `"presence observed"` + payload summary | Alembic migrations |
| Route prefix `/mvp/presence/*` (explicitly temporary) | Routes under `/attendance/presence/*` |
| Optional debug GET of recent entries | Dashboard static/JS changes |
| Process restart clears state | Correlation with RecognitionEventLog |

### E.4 Wire contract (MVP only — not in `shared/contracts.py`)

**Request:** `POST /mvp/presence/observed`

```json
{
  "estimated_count": 3,
  "timestamp_ms": 1716470400000
}
```

**Response:**

```json
{
  "accepted": true,
  "message": "presence observed",
  "estimated_count": 3
}
```

No `gallery_identity`. No `camera_id`. No `classroom_id`. No zones. No history API contract beyond optional debug GET.

### E.5 Occupancy algorithm boundary

- **One** hardcoded method inside `occupancy.py`.
- No CLI flag to choose ONNX vs HOG vs YOLO.
- Output is a non-negative integer (clamp at 0).
- Accuracy is not a Track 0.5 gate; proving the pipe is.

### E.6 Relationship to Pi auth

| Path | Coupling |
|------|----------|
| Pi → RecognitionEvent → AttendanceEngine | Independent |
| Laptop → MVP POST → in-memory log | Independent |
| Cross-link auth ↔ occupancy | **None in Track 0.5** |

Manual demo narrative: authenticate on Pi, wave at laptop webcam, observe two log lines in different subsystems. That satisfies MVP.

### E.7 Explicit deferrals (post–Track 0.5)

Restore from [`ARCHITECTURE.md`](ARCHITECTURE.md) in later tracks:

| Track | Scope |
|-------|-------|
| **1** | Persistent store, formal contract in `shared/contracts.py`, `camera_sources` role |
| **2** | Person detector ONNX, zones, observation log table |
| **3** | Correlator, PresenceEngine, PresenceRecord |
| **4** | Exceptions, dashboard panels |
| **5** | Deployment bundle, multi-classroom |

### E.8 Delete vs defer

- **Delete for MVP** means not built, not stubbed, not migrated.
- **Defer** means documented in full ARCHITECTURE but no files until a later track.
- **`ARCHITECTURE.md`** remains the north-star; this document overrides it for Track 0.5 execution only.

---

## Quick reference

```
Pi (frozen)                         Laptop (new)                    Cloud (minimal)
───────────                         ────────────                    ───────────────
YuNet → MobileFaceNet → router      webcam → occupancy.py           POST /mvp/presence/observed
→ AttendanceIngestionClient         → client.py                     → in-memory store
→ /attendance/recognition/events    → /mvp/presence/observed        → log "presence observed"
→ AttendanceEngine                  (no identity)                   (no DB)
→ Dashboard (unchanged)
```

---

*End of D3 Track 0.5 MVP plan.*
