# `config/` — edge runtime control plane

This directory holds **shared edge configuration**. The cloud ArcFace server (`cloud/main.py`) does **not** import these modules; see `docs/DEPLOYMENT.md` and `cloud/README.md`.

**Authoritative source:** `config/settings.py` — every tunable constant the edge pipeline reads at import time (plus a few values only snapshotted for experiments).

**Companion docs:**

| Topic | Document |
|-------|----------|
| Pass-9 stabilizers (defaults = historic behaviour) | `docs/STABILIZATION_KNOBS.md` |
| CSV layouts, session layout, consumers | `docs/TELEMETRY.md` |
| Pi vs cloud bundles, env examples | `docs/DEPLOYMENT.md`, `deployment/*/README.md` |
| Edge package map | `edge/README.md` |

**Hybrid cloud offload** is *not* defined here. The edge reads `CLOUD_SERVER_URL`, `CLOUD_ROUTING`, `CLOUD_THRESHOLD`, `CLOUD_FORCE_OFFLOAD`, `CLOUD_FORCE_EDGE`, `CLOUD_TIMEOUT_S`, `CLOUD_JPEG_QUALITY` from `os.environ` inside `edge/main.py` (see `edge/README.md` → Hybrid cloud).

---

## How settings load

1. **Import order:** `import config.settings` runs once; values are fixed for the process unless you reload the module (not supported in normal runs).
2. **Truthiness helpers:**
   - `_env_truthy(name, default)`: unset/empty/`0`/`false`/`False` → false; otherwise true. Used for `SIMULATE_PI`, `TELEMETRY`, `TELEMETRY_OVERLAY`, `DEBUG_FRAMES`, `AUTO_EXPERIMENT_REPORT`.
   - **Exceptions:** `HEADLESS` defaults to **on** if unset (`HEADLESS` missing → headless). `STREAM_VIDEO` only treats `1`/`true`/`True`/`yes` as on.
3. **Simulation coupling:** When `SIMULATE_PI=1`, `TARGET_LATENCY_MS` defaults to **65** ms unless overridden; when `SIMULATE_PI=0`, it defaults to **0** (no pacing sleep in headless loop).

---

## Settings snapshot (`experiments/exp_*/config/settings_snapshot.json`)

`config/experiment_session._SETTINGS_SNAPSHOT_KEYS` records most `settings` attributes **at session start**. It does **not** currently include:

- `DIAG_LOG_EVERY_N`
- `EMBED_CADENCE_N`
- `YUNET_CADENCE_N`

So experiment reproduction from the JSON alone may miss those three if they were overridden via environment.

---

## Control plane by subsystem

The sections below are indexed for navigation. Each bullet is **`NAME`** (env if any) — default — primary consumer — one-line effect.

### 1. Simulation, threading, loop pacing

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `SIMULATE_PI` | `SIMULATE_PI` | `0` | `edge.main`, import-time OpenCV | Laptop-style thread caps + default pacing when `1`. |
| `PI_MAX_THREADS` | `PI_MAX_THREADS` | `1` | `edge.main`, `SIMULATE_PI` block | Caps OpenCV/OMP/OpenBLAS/TF/TFLite threads when simulating. |
| `TARGET_LATENCY_MS` | `TARGET_LATENCY_MS` | `65` if sim else `0` | `edge.main` main loop | Minimum frame period (ms): sleep in GUI sim path; headless pacing when `>0`. |

**Dangerous interactions:** `SIMULATE_PI=1` on a real Pi wastes performance (artificial caps). `TARGET_LATENCY_MS>0` in production lowers effective FPS.

---

### 2. Detection (YuNet)

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `YUNET_INPUT_W` / `YUNET_INPUT_H` | both | `640` / `480` | `edge.main` | YuNet `setInputSize`; trades latency vs small-face recall. See `STABILIZATION_KNOBS.md`. |
| `YUNET_CADENCE_N` | `YUNET_CADENCE_N` | `1` | `edge.main` | Run `detect()` every N frames; reuse cached faces otherwise. Diagnostic column `yunet_cadence_skip`. |
| — | (hardcoded in code) | score 0.50, NMS 0.30 | `edge.main.__init__` | Not in `settings.py` (future control-plane gap). |

**Doc gap:** `YUNET_CADENCE_N` is **not** in `docs/STABILIZATION_KNOBS.md` (that doc only mentions `TARGET_LATENCY_MS` under “Cadence”).

---

### 3. Embedding / recognition thresholds

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `EMBED_CADENCE_N` | `EMBED_CADENCE_N` | `1` | `edge.main` | After liveness buffer full, run embed every N frames (stale embedding up to N frames). |
| `MATCH_HIGH_BASE` | — | `0.80` | `edge.pipeline_controller` | High match threshold base (adaptive logic may adjust). |
| `MATCH_MID_BASE` | — | `0.65` | `edge.pipeline_controller` | Mid tier threshold. |
| `SIM_EMA_ALPHA` | `SIM_EMA_ALPHA` | `0.0` | `edge.stabilization` + `edge.main` | EMA on similarity before thresholding; logged `sim` matches decision driver. |
| `MATCH_PERSISTENCE_FRAMES` | `MATCH_PERSISTENCE_FRAMES` | `1` | `edge.stabilization` | Consecutive MATCHED frames before attendance CSV/log row. |
| `LIVENESS_WINDOW` | — | `8` | `edge.main`, `edge.liveness` | Deque length for motion/texture history and embed buffer cap. |

**Doc gap:** `EMBED_CADENCE_N` absent from `STABILIZATION_KNOBS.md`. `MATCH_*_BASE` are hardcoded — changing them requires editing `settings.py` (no env).

---

### 4. PAD / liveness (5-tier tuning + motion)

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `PAD_SPOOF_STREAK_REQUIRED` | `PAD_SPOOF_STREAK_REQUIRED` | `1` | `edge.stabilization` | Consecutive SPOOF votes before pipeline accepts SPOOF (else UNCERTAIN). |
| `MOTION_MIN_THRESHOLD` | — | `0.35` | `edge.liveness` | `avg_mag` / magnitude gate for “moving”; affects rigid vs planar gates. |
| `RIGID_ANGLE_VAR_TH` | — | `0.15` | `edge.liveness` | Optical-flow angle variance threshold (per-frame rigidity + planar evidence). |
| `RIGID_MAG_VAR_TH` | — | `1.5` | `edge.liveness` | Magnitude variance threshold for rigidity / planar evidence. |
| `STATIC_AREA_VAR_TH` | — | `20.0` | `edge.liveness` | Area variance floor for static-depth / planar traps. |
| `SCREEN_LAPLACIAN_TH` | — | `80.0` | **none** | Intended for screen-glare gate; **dead** (assignment commented out in `liveness.py`). |
| `MAX_BRIGHTNESS_TH` | — | `180` | **none** | Same — glare gate **disabled**. |
| `MIN_SKIN_RATIO` | — | `0.15` | **none** | **Unused** in current `liveness.py` (skin scoring uses adaptive target, not this constant). |
| `UNREAL_AREA_VAR_TH` | — | `5000.0` | **none** | **Unused** (legacy “glitchy jump” reject — not referenced in engine). |

**Note:** Many PAD decisions still use **hardcoded** thresholds inside `edge/liveness.py` (e.g. rigid `0.85`/`0.95`, planar streak counts). Those are **not** in `settings.py` — the control plane is only partial.

---

### 5. Orientation / pose telemetry

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `ORIENTATION_OVERHEAD_TH` | `ORIENTATION_OVERHEAD_TH` | `0.60` | `edge.orientation` | `orient_ratio` bucket boundary → OVERHEAD. |
| `ORIENTATION_TILTED_TH` | `ORIENTATION_TILTED_TH` | `0.915` | `edge.orientation` | Upper bound for TILTED band. |
| `ORIENTATION_SMOOTHING_WINDOW` | `ORIENTATION_SMOOTHING_WINDOW` | `5` | `edge.orientation` | Majority-vote smoothing of `mode_raw`. |
| `POSE_TELEMETRY_MIN_IOU` | `POSE_TELEMETRY_MIN_IOU` | `0.12` | `edge.main` | When strict face match fails, still attach orientation telemetry if tracker–detector IoU exceeds this. `0` disables. |

**Naming:** `POSE_TELEMETRY_MIN_IOU` is orientation-telemetry association, not a separate pose estimator.

**History:** `docs/reference_experiment_analysis.md` — default OVERHEAD threshold often structurally unreachable; try `0.85`–`0.90`.

---

### 6. Geometric gating (distance)

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `K_FOCAL` | — | `100` | `edge.main` | Proxy depth: `distance = K_FOCAL / sqrt(face_w * face_h)`. |
| `MIN_DISTANCE` / `MAX_DISTANCE` | — | `0.4` / `3.0` | `edge.main` | Valid-face band in meters (proxy). |

**Deployment:** Recalibrate `K_FOCAL` after resolution or camera change — see `edge/camera.py` comments.

---

### 7. Telemetry, logging, I/O

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `EXPERIMENT_LABEL` | `EXPERIMENT_LABEL` | `""` | `edge.main`, CSVs | Tag column for diagnostics/telemetry; empty disables. |
| `VERBOSE_DEBUG` | `VERBOSE_DEBUG` | `0` | `run.py`, `edge.main`, `pipeline_controller` | Extra debug log volume (console policy via logging setup). |
| `LOG_BUFFER_SIZE` | — | `8192` | `edge.main`, `edge.telemetry` | `open(..., buffering=)`. |
| `LOG_FLUSH_INTERVAL` | `LOG_FLUSH_INTERVAL` | `30` | `edge.main` | Flush CSV buffers every N frames. |
| `DIAG_LOG_EVERY_N` | `DIAG_LOG_EVERY_N` | `1` | `edge.main` | Subsample diagnostic rows per track. **Not in snapshot keys.** |
| `DIAG_MAX_SIZE_MB` | — | `50.0` | **none** | Documented intent (rotate huge CSV) — **not implemented** in `edge/main.py` (only schema-based rotation exists). |
| `TELEMETRY` | `TELEMETRY` | `1` | `edge.main`, `edge.telemetry` | Master switch for frame `telemetry_log.csv` + overlay eligibility. |
| `TELEMETRY_OVERLAY` | `TELEMETRY_OVERLAY` | `0` | `edge.main` | On-screen telemetry strip when a display path exists. |
| `TELEMETRY_LOG_EVERY_N` | `TELEMETRY_LOG_EVERY_N` | `1` | `edge.main` | Subsample telemetry rows. |
| `TELEMETRY_DT_WINDOW` | `TELEMETRY_DT_WINDOW` | `30` | `edge.telemetry` | Rolling window for `dt_ms` mean/std. |
| `FPS_WINDOW` | — | `30` | `edge.main` | Rolling FPS deque length. |
| `PERF_SAMPLE_INTERVAL` | — | `10` | `edge.main` | Sample CPU/mem/temp every N frames. |
| `THERMAL_WARN_C` | `THERMAL_WARN_C` | `0` | `edge.main` | Log warning if CPU temp ≥ threshold; `0` disables. |
| `THERMAL_WARN_INTERVAL_S` | `THERMAL_WARN_INTERVAL_S` | `60` | `edge.main` | Throttle thermal warnings. |
| `AUTO_EXPERIMENT_REPORT` | `AUTO_EXPERIMENT_REPORT` | `1` | `edge.main` | Run post-session plots/summaries (`edge.experiment_report`). |

---

### 8. Overlays, display, streaming

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `HEADLESS` | `HEADLESS` | **`1` (headless)** | `edge.main` | Skip `imshow` / `waitKey` when true. |
| `STREAM_VIDEO` | `STREAM_VIDEO` | `0` | `edge.main`, `edge.stream_server` | Flask MJPEG server when `1`. |
| `STREAM_HOST` / `STREAM_PORT` | both | `0.0.0.0` / `5000` | `edge.stream_server` | Bind address/port. |
| `STREAM_JPEG_QUALITY` | `STREAM_JPEG_QUALITY` | `75` | `edge.main` | MJPEG encode quality. |

**Interaction:** `TELEMETRY_OVERLAY` only shows if `(not HEADLESS) or STREAM_VIDEO`.

---

### 9. Camera acquisition

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `CAMERA_BACKEND` | `CAMERA_BACKEND` | `libcamera_subprocess` | `edge.main` → `edge.camera` | Backend selector (`opencv`, `libcamera`, `picamera2`, …). |

**Note:** Capture resolution `640x480` @ `fps=15` is **hardcoded** in `edge.main` (`CameraSource(...)`), not `settings.py`.

---

### 10. BBox smoothing (post-match)

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `BBOX_EMA_ALPHA` | `BBOX_EMA_ALPHA` | `0.0` | `edge.stabilization` | EMA on bbox after association; affects crop-derived metrics in CSV. |

---

### 11. Debug frame capture

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `DEBUG_FRAMES` | `DEBUG_FRAMES` | `0` | `edge.main` | Enable JPEG dumps under session `debug_frames/`. |
| `DEBUG_FRAMES_DIR` | `DEBUG_FRAMES_DIR` | `""` | `edge.main` | Override directory (empty → session default). |
| `DEBUG_FRAMES_MIN_INTERVAL_S` | `DEBUG_FRAMES_MIN_INTERVAL_S` | `2.0` | `edge.main` | Min wall time between saves. |
| `DEBUG_FRAMES_MAX_PER_RUN` | `DEBUG_FRAMES_MAX_PER_RUN` | `500` | `edge.main` | Cap total dumps. |
| `DEBUG_SAMPLE_EVERY_N` | `DEBUG_SAMPLE_EVERY_N` | `0` | `edge.main` | Periodic sampled frames (`0` = off). |
| `DEBUG_YUNET_SCORE_TH` | `DEBUG_YUNET_SCORE_TH` | `0` | `edge.main` | Save when YuNet score below threshold (`0` = disable this trigger). |
| `DEBUG_JPEG_QUALITY` | `DEBUG_JPEG_QUALITY` | `88` | `edge.main` | JPEG quality for debug dumps. |

---

### 12. Experiments / labels (no protocol schema here)

| Name | Env | Default | Owner | Effect |
|------|-----|---------|-------|--------|
| `EXPERIMENT_LABEL` | `EXPERIMENT_LABEL` | `""` | CSV + cloud client metadata | Free-form session tag for analysis splits. |
| `CAMERA_MODE` | — | `"tilted"` | **none** | Snapshotted only — **no runtime read** in edge code. |

Structured experiment metadata (`experiment_protocol.json`) is documented in `docs/EXPERIMENT_PROTOCOL.md` and is **not** a field in `settings.py`.

---

## Known gaps and hygiene issues

| Issue | Detail |
|-------|--------|
| Dead / misleading settings | `CAMERA_MODE`, `DIAG_MAX_SIZE_MB`, `SCREEN_LAPLACIAN_TH`, `MAX_BRIGHTNESS_TH`, `MIN_SKIN_RATIO`, `UNREAL_AREA_VAR_TH` — see subsystem sections. |
| Snapshot incompleteness | `DIAG_LOG_EVERY_N`, `YUNET_CADENCE_N`, `EMBED_CADENCE_N` missing from `_SETTINGS_SNAPSHOT_KEYS`. |
| Doc drift | `STABILIZATION_KNOBS.md` omits `YUNET_CADENCE_N`, `EMBED_CADENCE_N`, `DIAG_LOG_EVERY_N`; claims snapshot lists “every knob”. |
| Partial PAD control plane | Critical liveness thresholds live as literals in `edge/liveness.py`, not in `settings.py`. |

---

## Maintainer checklist

When adding a new `settings` value:

1. Document it in **this file** and, if pass-9-style, in `docs/STABILIZATION_KNOBS.md`.
2. Append the key to `_SETTINGS_SNAPSHOT_KEYS` in `config/experiment_session.py` if it affects reproducibility.
3. If it changes CSV meaning, update `docs/TELEMETRY.md` **after** updating `DIAG_COLUMNS` / `TELEMETRY_CSV_COLUMNS` in code (code is authoritative per `TELEMETRY.md`).
