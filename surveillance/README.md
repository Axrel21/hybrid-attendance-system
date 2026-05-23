---

# Validation

Before running the live prototype, validate imports and runtime dependencies.

## 1. Compile Check

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

## 2. Occupancy Logic Smoke Test

Verify occupancy estimation runs independently of webcam access.

Run:

```bash
python3 -c "
from surveillance.occupancy import estimate_occupancy
import numpy as np

frame = np.zeros((240,320,3),dtype=np.uint8)

count = estimate_occupancy(frame)

print('occupancy=', count)

assert count == 0

print('occupancy_ok')
"
```

Expected:

```text
occupancy= 0
occupancy_ok
```

This confirms:

- package imports work
- OpenCV loads
- occupancy pipeline executes
- blank frames produce zero detections

---

## 3. Runtime Smoke Test

Launch local surveillance runtime.

```bash
python -m surveillance.run
```

Expected behavior:

```text
webcam opens

↓

live preview visible

↓

overlay updates:

Occupancy: N

↓

press q

↓

clean exit
```

Success criteria:

- no crashes
- occupancy updates continuously
- camera closes correctly
- application exits immediately

---

# Troubleshooting

## Window does not open

Check OpenCV build:

```bash
python -c "
import cv2
print(cv2.__version__)
"
```

If using:

```text
opencv-python-headless
```

remove it:

```bash
pip uninstall opencv-python-headless
pip install opencv-python
```

---

## Camera cannot be opened

Verify webcam availability.

Linux:

```bash
ls /dev/video*
```

Expected:

```text
/dev/video0
```

---

## Import errors

Run from repository root only.

Correct:

```bash
python -m surveillance.run
```

Avoid:

```bash
python surveillance/run.py
```

---

# Exit Criteria (Track 1 Complete)

Track 1 is considered complete when all conditions pass:

- compile check passes
- occupancy smoke test passes
- runtime launches
- occupancy overlay updates
- clean quit works
- no network requests occur
- D1 and D2 remain unchanged