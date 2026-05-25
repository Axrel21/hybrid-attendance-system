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
