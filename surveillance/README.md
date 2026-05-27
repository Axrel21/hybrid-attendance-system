---

## Validation & Smoke Tests

Run these checks before considering Track 3 complete.

### 1. Compile Check

Verify all surveillance modules load correctly.

```bash
python3 -m compileall surveillance
```

Expected:

```text
Listing 'surveillance'...
Compiling ...
```

No errors should appear.

---

### 2. Tracker Smoke Test

Verify tracking pipeline runs without webcam input.

Install surveillance dependencies first (see [Track 2 dependencies](#track-2--occupancy-quality)).

```bash
python3 -c "
from surveillance.occupancy import estimate_occupancy, get_active_track_ids
import numpy as np

frame = np.zeros((240, 320, 3), dtype=np.uint8)

assert estimate_occupancy(frame) == 0
assert get_active_track_ids() == []

print('tracker_ok')
"
```

Expected:

```text
tracker_ok
```

First run downloads `yolov8n.pt` (~6 MB) and loads the model; subsequent runs reuse the cached weights.

Confirms:

- imports work
- YOLOv8n + ByteTrack load lazily on first inference
- blank frame returns zero occupancy and no active track IDs

---

### 3. Runtime Smoke Test

Start surveillance runtime.

```bash
python -m surveillance.run
```

Expected flow:

```text
webcam opens

↓

live preview

↓

Occupancy: N

↓

press q

↓

clean shutdown
```

Success criteria:

- preview renders
- bounding boxes and `#<track_id>` labels on visible people
- `Occupancy: N` matches count of active tracks (not raw detections)
- track IDs persist while a person stays in frame; removed when they leave
- no crashes
- camera releases correctly
- no backend or attendance traffic

---

## Troubleshooting

### Import errors

Install dependencies:

```bash
pip install -r surveillance/requirements-surveillance.txt
```

Run from repository root:

```bash
python -m surveillance.run
```

Avoid:

```bash
python surveillance/run.py
```

---

### Window does not open

Check OpenCV:

```bash
python -c "
import cv2
print(cv2.__version__)
"
```

If using headless build:

```bash
pip uninstall opencv-python-headless
pip install opencv-python
```

---

### Camera unavailable

Linux:

```bash
ls /dev/video*
```

Expected:

```text
/dev/video0
```

---

### Model download fails

Ultralytics downloads `yolov8n.pt` on first inference. Ensure outbound HTTPS is allowed once, or place the file manually where Ultralytics expects it (typically `~/.cache/ultralytics/` or the working directory).

---

## Exit Criteria (Track 1 Complete)

Track 1 is complete when:

- compile check passes
- occupancy smoke test passes
- runtime launches
- occupancy overlay updates
- clean quit works
- no network activity occurs (except optional one-time model download for Track 2)
- D1/D2 remain unchanged

---

## Track 2 — Occupancy Quality

Track 2 replaces OpenCV HOG with **YOLOv8n** inside `occupancy.py` only. Track 1 runtime shape is unchanged: local webcam, scalar overlay, `python -m surveillance.run`, no backend.

### Pipeline

```text
camera frame
  → YOLOv8n (class=person, CPU)
  → count(detections)
  → overlay
```

### Dependency changes

Install from repo root:

```bash
pip install -r surveillance/requirements-surveillance.txt
```

| Package | Role |
|---------|------|
| `ultralytics` | YOLOv8n inference (pulls CPU `torch`) |
| `opencv-python` | Webcam preview in `run.py` |
| `numpy` | Frame arrays |

Track 1 HOG required only OpenCV + numpy. Track 2 adds Ultralytics/PyTorch for better seated and partial-body detection.

### Model notes

- Weights: `yolov8n.pt` (nano — smallest YOLOv8 variant).
- Loaded **lazily** on the first `estimate_occupancy()` call; one process-wide instance.
- **CPU only** — `device="cpu"`; no GPU required.
- **Inference size** — `imgsz=320` matches webcam width (320×240 capture); avoids default 640 upscaling and lowers CPU cost.
- **Person class only** — COCO class `0`; occupancy = detection count.
- **Confidence** — default `0.35`; override with env `SURVEILLANCE_CONFIDENCE` (float, e.g. `0.25`).

No tracking, identities, zones, or attendance coupling.

### CPU utilization notes

- YOLOv8n on CPU at 320×240 is heavier than Track 1 HOG; expect **moderate to high CPU** (often 40–90% of one core on a laptop, varies by hardware).
- Preview may run below real-time frame rate; occupancy still updates each processed frame.
- Lower load: raise `SURVEILLANCE_CONFIDENCE` slightly, close other heavy apps, or use a machine with more CPU headroom.
- GPU is intentionally not used.

### Rollback instructions

To revert to Track 1 HOG occupancy:

1. Restore `surveillance/occupancy.py` from git before Track 2:
   ```bash
   git checkout HEAD -- surveillance/occupancy.py
   ```
   (Or restore the HOG version from your Track 1 commit.)

2. Optional — remove Track 2 Python packages:
   ```bash
   pip uninstall ultralytics torch torchvision -y
   ```

3. Re-run compile and smoke tests from the [Track 1](#exit-criteria-track-1-complete) section (OpenCV + numpy only).

`run.py` and `camera.py` are unchanged; rollback is confined to `occupancy.py` and optional deps.

### Track 2 validation

```bash
python3 -m compileall surveillance

python3 -c "
from surveillance.occupancy import estimate_occupancy
import numpy as np
frame = np.zeros((240, 320, 3), dtype=np.uint8)
assert estimate_occupancy(frame) == 0
print('occupancy_ok')
"

python -m surveillance.run
```

Live check: seated or partially visible occupants should be detected more reliably than Track 1 HOG.

---

## Track 3 — Person Tracking & Presence Persistence

Track 3 adds **ByteTrack** on top of Track 2 YOLOv8n inside `occupancy.py` only. `run.py` and `camera.py` are unchanged.

### Pipeline

```text
camera frame
  → YOLOv8n detect (class=person, CPU, imgsz=320)
  → ByteTrack (persist=True)
  → unique active track IDs
  → occupancy = len(active tracks)
  → overlay (boxes, Track IDs list; run.py adds Occupancy line)
```

### Rules

| Allowed | Forbidden |
|---------|-----------|
| Anonymous numeric track IDs (`#2`, `#5`) | Names, gallery, embeddings |
| Local runtime persistence (`persist=True`) | Attendance, cloud POST, classroom mapping |
| Count unique tracks in current frame | Identity, ArcFace, MobileFaceNet |

Track IDs reset on process restart. They are **not** student identities.

### Dependency changes

`lap` is required for ByteTrack inside Ultralytics:

```bash
pip install -r surveillance/requirements-surveillance.txt
```

### Expected tracking behavior

1. **Person enters frame** — YOLO detects person; ByteTrack assigns a new numeric ID (e.g. `#3`).
2. **Person remains visible** — same ID persists across frames (e.g. frame 1 and frame 40 both show `#3`).
3. **Second person enters** — second ID (e.g. `#8`); occupancy becomes `2`.
4. **Person leaves frame** — their track drops from active set; occupancy decreases after ByteTrack drops the track.
5. **Runtime restart** — all IDs reset; numbering may differ from previous session.

Overlay (drawn in `occupancy.py` on the frame; `run.py` still draws `Occupancy: N` at the top):

```text
Occupancy: 3

Track IDs:
#2
#5
#11
```

Plus orange boxes with `#<id>` on each person.

### CPU utilization notes

- ByteTrack adds modest CPU on top of YOLOv8n inference (association is lightweight vs detection).
- Expect similar range to Track 2: **moderate to high CPU** on one core at 320×240.
- Preview may remain below real-time; tracking still advances each processed frame.

### Rollback instructions

To revert to Track 2 (detection count only, no tracking):

1. Restore Track 2 `surveillance/occupancy.py` from git.
2. Optional: `pip uninstall lap -y` if not needed elsewhere.
3. Re-run [Track 2 validation](#track-2-validation).

### Track 3 validation

```bash
python3 -m compileall surveillance

python3 -c "
from surveillance.occupancy import estimate_occupancy, get_active_track_ids
import numpy as np
frame = np.zeros((240, 320, 3), dtype=np.uint8)
assert estimate_occupancy(frame) == 0
assert get_active_track_ids() == []
print('tracker_ok')
"

python -m surveillance.run
```

Live check: stand in frame — note your track ID; move slightly — ID unchanged; step out — ID removed from list and occupancy drops.

---

## Track 4 — Cloud Presence Transport

Track 4 adds **SurveillancePresenceClient** and **PresenceSync** so anonymous presence events reach the cloud. No attendance, identity, or classroom logic.

### Pipeline

```text
camera → YOLO → ByteTrack → presence sync → POST /presence/events → in-memory log
```

`run.py` calls `presence.observe()` after each frame. Overlay and tracking behavior from Track 3 are unchanged.

### Wire contract

`POST /presence/events`

```json
{
  "camera_id": "surveillance-laptop-01",
  "track_id": 3,
  "event": "appeared",
  "timestamp_ms": 1716470400000,
  "occupancy": 2
}
```

Events: `appeared`, `disappeared`, `heartbeat` (heartbeat uses `track_id: 0`).

Backend logs `presence event received` and stores events in memory only — **does not** call `AttendanceEngine`.

### Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `SURVEILLANCE_PRESENCE_ENABLED` | `1` | Set `0` to disable POST |
| `SURVEILLANCE_PRESENCE_API_URL` | `{CLOUD_SERVER_URL}/presence/events` | Full POST URL |
| `SURVEILLANCE_CAMERA_ID` | `surveillance-laptop-01` | Camera label |
| `SURVEILLANCE_PRESENCE_TIMEOUT_S` | `1.0` | HTTP timeout |
| `SURVEILLANCE_PRESENCE_BATCH_SIZE` | `0` | `0` = immediate; `N>1` = batch N then flush |
| `SURVEILLANCE_HEARTBEAT_S` | `30` | Heartbeat interval |
| `CLOUD_SERVER_URL` | `http://localhost:8000` | Base when API URL unset |

### Dependency changes

```bash
pip install -r surveillance/requirements-surveillance.txt
```

Adds `requests` for HTTP transport.

Composite backend must mount `presence_router` (included in `cloud_backend/server.py`).

### Track 4 validation

**1. Compile check**

```bash
python3 -m compileall surveillance cloud_backend/attendance
```

**2. Local POST smoke test** (backend running on port 8000)

```bash
curl -s -X POST http://localhost:8000/presence/events \
  -H 'Content-Type: application/json' \
  -d '{
    "camera_id": "surveillance-laptop-01",
    "track_id": 3,
    "event": "appeared",
    "timestamp_ms": 1716470400000,
    "occupancy": 1
  }'
```

Expected JSON includes `"accepted": true` and `"message": "presence event received"`. Backend log line: `presence event received`.

**3. Runtime command**

```bash
# Terminal 1 — backend
bash deployment/cloud/run_backend.sh --host 0.0.0.0 --port 8000

# Terminal 2 — surveillance
export CLOUD_SERVER_URL=http://localhost:8000
python -m surveillance.run
```

**4. Failure behavior**

- Backend down / timeout / connection refused: warning logged; preview loop continues.
- `SURVEILLANCE_PRESENCE_ENABLED=0`: no HTTP traffic.
- Client **never raises** into the runtime loop.

**5. Rollback**

1. Set `SURVEILLANCE_PRESENCE_ENABLED=0`, or restore Track 3 `surveillance/run.py` (no presence imports).
2. Remove `app.include_router(presence_router)` from `cloud_backend/server.py` if reverting backend route.
3. Optional: `pip uninstall requests -y` if unused elsewhere.

Track 3 tracking-only behavior remains if presence is disabled without code rollback.

---

## Track 5 — Presence Timeline Aggregation

Track 5 adds **PresenceTimelineService** on the cloud side. Raw events from Track 4 are aggregated into anonymous **presence sessions** (in-memory only). No DB, identity, classroom logic, or `AttendanceEngine` calls.

### Pipeline

```text
POST /presence/events → event log + PresenceTimelineService
GET  /presence/sessions → { total, sessions[] }
```

### Session rules

| Event | Effect |
|-------|--------|
| `appeared` (track_id > 0) | Create active session (`first_seen`, `last_seen`) |
| `heartbeat` | Refresh `last_seen` for all **active** sessions on that `camera_id` |
| `disappeared` | Set `inactive`, update `last_seen` |
| Timeout | If `last_seen` older than `PRESENCE_SESSION_TIMEOUT_S` (default 45s), mark `inactive` on ingest or GET |

`duration_sec = (last_seen - first_seen) // 1000`. Track IDs stay anonymous.

### Read API

```bash
curl -s http://localhost:8000/presence/sessions
curl -s http://localhost:8000/presence/sessions/surveillance-laptop-01
```

Example session:

```json
{
  "camera_id": "surveillance-laptop-01",
  "track_id": 7,
  "first_seen": 1716470700000,
  "last_seen": 1716473280000,
  "duration_sec": 2580,
  "status": "inactive"
}
```

Convert `first_seen` / `last_seen` ms to local time for display (e.g. 09:05 / 09:48).

### Track 5 validation

**1. Compile check**

```bash
python3 -m compileall cloud_backend/attendance
```

**2. Local POST test** (create session)

```bash
curl -s -X POST http://localhost:8000/presence/events \
  -H 'Content-Type: application/json' \
  -d '{
    "camera_id": "surveillance-laptop-01",
    "track_id": 7,
    "event": "appeared",
    "timestamp_ms": 1716470700000,
    "occupancy": 1
  }'
```

**3. Session query test**

```bash
curl -s http://localhost:8000/presence/sessions | python3 -m json.tool
```

Expected: `total` ≥ 1, session with `track_id: 7`, `status: "active"`, `duration_sec` ≥ 0.

Advance timeline:

```bash
curl -s -X POST http://localhost:8000/presence/events \
  -H 'Content-Type: application/json' \
  -d '{
    "camera_id": "surveillance-laptop-01",
    "track_id": 7,
    "event": "heartbeat",
    "timestamp_ms": 1716471900000,
    "occupancy": 1
  }'

curl -s http://localhost:8000/presence/sessions/surveillance-laptop-01 | python3 -m json.tool
```

Expected: `last_seen` updated, `duration_sec` increased.

**4. Timeout behavior**

POST `disappeared` or wait longer than `PRESENCE_SESSION_TIMEOUT_S` without heartbeat, then GET sessions — `status` should be `inactive`.

```bash
curl -s -X POST http://localhost:8000/presence/events \
  -H 'Content-Type: application/json' \
  -d '{
    "camera_id": "surveillance-laptop-01",
    "track_id": 7,
    "event": "disappeared",
    "timestamp_ms": 1716473280000,
    "occupancy": 0
  }'
```

**5. Rollback**

Remove timeline wiring from `presence_handler.py` and delete or bypass `presence_timeline.py` / GET routes in `presence_api.py`. Track 4 event POST and raw event store continue to work.

### Files (Track 5)

| File | Role |
|------|------|
| `cloud_backend/attendance/presence_timeline.py` | `PresenceTimelineService` |
| `cloud_backend/attendance/presence_handler.py` | Feeds timeline on ingest |
| `cloud_backend/attendance/presence_api.py` | GET `/presence/sessions` |
| `cloud_backend/attendance/schemas/presence.py` | Session response models |

Surveillance runtime (`run.py`, `occupancy.py`) unchanged.

---

## D4 Track 1 — Attendance Evidence (cloud)

Correlation lives in `cloud_backend/attendance/` only. Combines recognition log rows with in-memory presence sessions into **evidence** (not final attendance).

### API

```bash
curl -s http://localhost:8000/attendance/evidence | python3 -m json.tool
curl -s http://localhost:8000/attendance/evidence/{lecture_id} | python3 -m json.tool
```

### Evidence rules

| Condition | `evidence` | `confidence` |
|-----------|------------|--------------|
| Recognition + presence session in same classroom (surveillance cameras) | `presence_observed` | `medium` / `high` |
| Recognition, no presence | `recognized_only` | `low` |
| Invalid / empty identity | `unknown` | `low` |

Register surveillance cameras in `camera_sources` with `meta_json` `{"role":"surveillance"}` or `surv_*` camera_id prefix.

### D4 validation

```bash
python3 -m compileall cloud_backend/attendance

# POST presence + ensure recognition logs exist, then:
curl -s http://localhost:8000/attendance/evidence | python3 -m json.tool
```

**Rollback:** remove `evidence_router` from `cloud_backend/server.py` and delete `evidence_*.py` under `cloud_backend/attendance/`.

---

## D4 Track 2 — Attendance Eligibility (cloud)

Read-only advisory from evidence + presence session duration vs lecture duration. **No** `AttendanceEngine`, state transitions, or attendance confirmation.

### Formula

```text
presence_ratio = presence_duration_sec / lecture_duration_sec
```

Default threshold: `ATTENDANCE_ELIGIBILITY_THRESHOLD=0.80`

| `decision` | Condition |
|------------|-----------|
| `eligible` | `presence_observed` and ratio ≥ threshold (e.g. 52/60) |
| `insufficient_presence` | `presence_observed` and ratio < threshold (e.g. 20/60) |
| `unknown` | `recognized_only` or missing lecture span |

### API

```bash
curl -s http://localhost:8000/attendance/eligibility | python3 -m json.tool
curl -s http://localhost:8000/attendance/eligibility/{lecture_id} | python3 -m json.tool
```

### D4 Track 2 validation

```bash
python3 -m compileall cloud_backend/attendance

curl -s http://localhost:8000/attendance/evidence | python3 -m json.tool
curl -s http://localhost:8000/attendance/eligibility | python3 -m json.tool
```

**Edge cases:** no lecture_id → `unknown`; lecture duration 0 → `unknown`; no presence sessions → `recognized_only` evidence → `unknown`.

**Rollback:** remove `eligibility_router` from `cloud_backend/server.py` and delete `eligibility_*.py` + `schemas/eligibility.py`.

---

## D4 Track 3 — Strengthened Evidence Correlation

Classroom-level correlation now:

1. Resolves classroom from recognition log, lecture, or entry camera registry
2. Finds surveillance cameras for that classroom (registry `role=surveillance` or `surv_*` prefix)
3. Falls back to live presence `camera_id` values when registry is empty (`EVIDENCE_PRESENCE_CAMERA_FALLBACK=1`, default)
4. Picks the **strongest** presence session (overlap → active → longest `duration_sec`)
5. Emits `presence_observed` with `presence_duration_sec` populated

Optional env:

```bash
export EVIDENCE_SURVEILLANCE_CAMERA_IDS=surveillance-laptop-01
```

### D4 Track 3 validation

```bash
python3 -m compileall cloud_backend/attendance

# Create presence session
curl -s -X POST http://localhost:8000/presence/events \
  -H 'Content-Type: application/json' \
  -d '{
    "camera_id": "surveillance-laptop-01",
    "track_id": 7,
    "event": "appeared",
    "timestamp_ms": 1716470700000,
    "occupancy": 1
  }'

curl -s http://localhost:8000/presence/sessions | python3 -m json.tool
curl -s http://localhost:8000/attendance/evidence | python3 -m json.tool
curl -s http://localhost:8000/attendance/eligibility | python3 -m json.tool
```

Expected evidence: `presence_observed`, non-zero `presence_duration_sec`. Expected eligibility: `presence_ratio` > 0 when lecture duration is set.

**Rollback:** revert `evidence_service.py`, `evidence_queries.py`, and `schemas/evidence.py` to Track 1 versions.

---

## D4 Track 4 — Temporal Evidence Scoring

Adds `TemporalEvidenceScorer` to the existing evidence pipeline. Does not reject evidence; only adjusts `confidence` and exposes `time_delta_sec`.

### Scoring

```text
time_delta_sec = |recognized_at - presence.first_seen| / 1000
```

| `time_delta_sec` | `confidence` |
|------------------|--------------|
| ≤ 30 | `high` |
| ≤ 120 | `medium` |
| > 120 | `low` |

Correlation window (session pick): `EVIDENCE_TEMPORAL_WINDOW_SEC` (default **300**). Prefers presence sessions whose span overlaps recognition ± window.

### D4 Track 4 validation

```bash
python3 -m compileall cloud_backend/attendance

curl -s -X POST http://localhost:8000/presence/events \
  -H 'Content-Type: application/json' \
  -d '{
    "camera_id": "surveillance-laptop-01",
    "track_id": 7,
    "event": "appeared",
    "timestamp_ms": 1716470700000,
    "occupancy": 1
  }'

curl -s http://localhost:8000/attendance/evidence | python3 -m json.tool
```

Expected `presence_observed` record includes `time_delta_sec` and temporal `confidence` (`high` when recognition is within 30s of `first_seen`).

**Rollback:** remove `temporal_scorer.py` and revert temporal fields from `evidence_service.py` / `schemas/evidence.py`.

---

## D5 Track 1 — Attendance Decisions (cloud)

Read-only decision layer over eligibility + evidence confidence. **Does not** call `AttendanceEngine` or write attendance records.

### Decision rules

| Eligibility | Confidence | Decision | Reason |
|-------------|------------|----------|--------|
| `eligible` | `high` | `present` | `eligible_high_confidence` |
| `eligible` | `medium` | `present` | `eligible_medium_confidence` |
| `eligible` | `low` | `manual_review` | `eligible_low_confidence` |
| `insufficient_presence` | * | `absent` | `insufficient_presence` |
| `unknown` | * | `manual_review` | `unknown_eligibility` |

### API

```bash
curl -s http://localhost:8000/attendance/decisions | python3 -m json.tool
curl -s http://localhost:8000/attendance/decisions/{lecture_id} | python3 -m json.tool
```

### D5 Track 1 validation

```bash
python3 -m compileall cloud_backend/attendance

curl -s http://localhost:8000/attendance/eligibility | python3 -m json.tool
curl -s http://localhost:8000/attendance/decisions | python3 -m json.tool
```

**Rollback:** remove `decision_router` from `cloud_backend/server.py` and delete `decision_*.py` + `schemas/decision.py`.

---

## D5 Track 2 — Derived Attendance States (cloud)

**AttendanceStateService** maps advisory decisions to a **derived state layer** (in-memory, recomputable). Does **not** write `attendance_records`, call `AttendanceEngine`, or change recognition/evidence/presence/eligibility.

### State transitions

| Decision | Derived `attendance_state` | Reason tag |
|----------|--------------------------|------------|
| `present` | `confirmed` | `decision_present` |
| `absent` | `insufficient_presence` | `decision_absent` |
| `manual_review` | `manual_review` | `decision_manual_review` |
| missing / other | `candidate` | `missing_decision` |

`expired` is reserved in the schema for future lecture-closure logic (not set in Track 2).

### API

```bash
curl -s http://localhost:8000/attendance/decisions | python3 -m json.tool
curl -s http://localhost:8000/attendance/states | python3 -m json.tool
curl -s http://localhost:8000/attendance/states/{lecture_id} | python3 -m json.tool
```

### D5 Track 2 validation

```bash
python3 -m compileall cloud_backend/attendance
```

**Rollback:** remove `state_router` from `cloud_backend/server.py` and delete `state_service.py`, `state_store.py`, `state_api.py`, `schemas/derived_state.py`.

---

## D5 Track 3 — Lecture Finalization (cloud)

**AttendanceFinalizationService** freezes derived states when a lecture reaches `finalized` status.

### Behavior

| Lecture status | States |
|----------------|--------|
| `active_window_open` (and other non-final) | Recomputed from decisions each request; `finalized: false` |
| `finalized` | Snapshot frozen on first query after end; `finalized: true` |

### Closure rules (at freeze)

| Pre-freeze state | Final state |
|------------------|-------------|
| `candidate` | `expired` |
| `confirmed` | unchanged |
| `manual_review` | unchanged |
| `insufficient_presence` | unchanged |

### API

```bash
curl -s http://localhost:8000/attendance/states | python3 -m json.tool
curl -s http://localhost:8000/attendance/finalized | python3 -m json.tool
curl -s http://localhost:8000/attendance/finalized/{lecture_id} | python3 -m json.tool
```

### D5 Track 3 validation

```bash
python3 -m compileall cloud_backend/attendance
```

**Lecture closure:** finalize lecture via existing session API, then `GET /attendance/finalized/{lecture_id}` — expect `finalized: true` and `candidate` → `expired`.

**Rollback:** remove `finalization_router` from `server.py`; delete `finalization_*.py`, `schemas/finalized.py`.
