# config/settings.py
import os


def _env_truthy(name: str, default: str = "0") -> bool:
    """True unless value is unset/empty or one of 0/false/False."""
    return os.environ.get(name, default) not in ("0", "false", "False", "")

# ---------------------------------------------------------------------
# Simulation vs deployment
# ---------------------------------------------------------------------
# SIMULATE_PI=1 — laptop / lab only: single-thread (or PI_MAX_THREADS) caps for
# OpenCV, OMP, OpenBLAS, TensorFlow (if present), and TFLite interpreter
# (see edge.main), plus default TARGET_LATENCY_MS for paced runs.
#
# Default SIMULATE_PI=0 — real Raspberry Pi and normal dev: no artificial
# thread caps at import time; libraries use their defaults.
#
#   Laptop Pi-like benchmarking:  SIMULATE_PI=1
#   Raspberry Pi production:       (omit or SIMULATE_PI=0)
SIMULATE_PI = _env_truthy("SIMULATE_PI", "0")

# Used only when SIMULATE_PI=1 (edge.main gates interpreter / cv2.apply).
PI_MAX_THREADS = max(1, int(os.environ.get("PI_MAX_THREADS", "1")))

# Frame pacing budget (ms). When SIMULATE_PI=1 defaults to 65 (CLI can override).
# When SIMULATE_PI=0 defaults to 0 so the headless loop does not sleep to pad
# every frame; GUI mode uses waitKey(1) unless simulating.
_default_target_ms = "65" if SIMULATE_PI else "0"
TARGET_LATENCY_MS = max(0, int(os.environ.get("TARGET_LATENCY_MS", _default_target_ms)))

try:
    import cv2

    if SIMULATE_PI:
        cv2.setNumThreads(PI_MAX_THREADS)
except ImportError:
    pass

CAMERA_MODE = "tilted"   # "tilted" (overhead) or "flat" (frontal)

# Base Matching Thresholds (Adaptive logic will modify these)
MATCH_HIGH_BASE = 0.80
MATCH_MID_BASE = 0.65

# Distance Filtering (Meters)
K_FOCAL = 100           # was 50 at 320x240 capture. Capture is now 640x480, so
                        # f_px doubles linearly => K_FOCAL = f_px * sqrt(W*H*cos θ)
                        # also doubles. ~60° HFOV webcam assumption; refit
                        # empirically with K = D_slant * sqrt(fw*fh) at a known
                        # standing distance after the resolution change settles.
MIN_DISTANCE = 0.4      # min usable approach at 7 ft elevated, tilted mount.
MAX_DISTANCE = 3.0      # upper bound; with K_FOCAL=100 and bbox ~33px at 3m,
                        # face still clears utils.py's 36px validation floor.

# 5-Tier Liveness Tuning
LIVENESS_WINDOW = 8
RIGID_ANGLE_VAR_TH = 0.15
RIGID_MAG_VAR_TH = 1.5
SCREEN_LAPLACIAN_TH = 80.0
STATIC_AREA_VAR_TH = 20.0      # was 50.0; narrows the photo-frozen trap to truly static bboxes.
                               # With K_FOCAL=50, real still-face area_var ≈ 25–80; trap was firing on
                               # legitimate users.
UNREAL_AREA_VAR_TH = 5000.0    # Reject glitchy tracking jumps
MIN_SKIN_RATIO = 0.15          # Reject screens/masks with low skin
MAX_BRIGHTNESS_TH = 180
# NOTE: duplicate SCREEN_LAPLACIAN_TH = 80 removed (was silently overwriting the 80.0 above)

# Single-thread / low-thread simulation: only when SIMULATE_PI=1.
if SIMULATE_PI:
    _t = str(PI_MAX_THREADS)
    os.environ["OMP_NUM_THREADS"] = _t
    os.environ["OPENBLAS_NUM_THREADS"] = _t
    os.environ["TF_NUM_INTRAOP_THREADS"] = _t
    os.environ["TF_NUM_INTEROP_THREADS"] = _t
    try:
        import tensorflow as tf

        tf.config.threading.set_inter_op_parallelism_threads(PI_MAX_THREADS)
        tf.config.threading.set_intra_op_parallelism_threads(PI_MAX_THREADS)
    except Exception:
        pass

# Liveness Motion Tuning
MOTION_MIN_THRESHOLD = 0.35    # was 0.5; lets near-still real users qualify as "moving" so they
                               # escape Gate C (which requires NOT is_moving).
                               # NOTE: this also feeds analyze_motion's per-frame is_rigid flag, so it
                               # mildly increases Gate A pressure. Combined with Tier 2, net REAL
                               # acceptance still improves. If you observe rigid_ratio drift upward
                               # for REAL after this change, revert to 0.5.

# =====================================================================
# Orientation / Pose Heuristic (validation + calibration knobs)
# =====================================================================
# The orientation subsystem in edge/orientation.py classifies each face
# into FRONTAL / TILTED / OVERHEAD using the geometric ratio
#     ratio = vertical_dist(eye_center -> mouth_center) / eye_dist
# Centralised here so the thresholds can be calibrated experimentally
# from data/diagnostic_log.csv without touching the runtime code.
#
#   ratio < ORIENTATION_OVERHEAD_TH               -> OVERHEAD
#   ORIENTATION_OVERHEAD_TH <= ratio < ORIENTATION_TILTED_TH -> TILTED
#   ratio >= ORIENTATION_TILTED_TH                -> FRONTAL
#
# Defaults (0.60 / 0.90) preserve the original behaviour exactly.
ORIENTATION_OVERHEAD_TH = 0.60
ORIENTATION_TILTED_TH   = 0.915
ORIENTATION_SMOOTHING_WINDOW = 5  # majority-vote temporal window length

# Free-form label stamped on every diagnostic row for the duration of a
# capture session. Pulled from the EXPERIMENT_LABEL env var so a single
# command-line export ('frontal_2m', 'overhead_3m', 'tilted_close', ...)
# tags every row in data/diagnostic_log.csv for that run, making them
# separable in offline analysis. Empty string disables the tag.
EXPERIMENT_LABEL = os.environ.get("EXPERIMENT_LABEL", "")

# =====================================================================
# Diagnostic Print Verbosity
# =====================================================================
# Per-frame [REC]/[DEBUG] prints are useful when iterating but pollute
# logs during multi-minute calibration sessions. Toggle via env var so
# experimental runs stay quiet without code edits:
#     $env:VERBOSE_DEBUG=0   (PowerShell)  - silence per-frame prints
#     $env:VERBOSE_DEBUG=1                  - keep them (default)
# Structured CSV logging is unaffected either way.
VERBOSE_DEBUG = os.environ.get("VERBOSE_DEBUG", "1") not in ("0", "false", "False", "")

# =====================================================================
# Raspberry Pi deployment flags
# =====================================================================
# HEADLESS — skip all cv2.imshow / cv2.namedWindow / cv2.waitKey calls.
# Required when running without a connected display (SSH, systemd service).
# Set via env var to avoid code edits:
#     export HEADLESS=1   (bash)   $env:HEADLESS=1 (PowerShell)
HEADLESS = os.environ.get("HEADLESS", "0") not in ("0", "false", "False", "")

# STREAM_VIDEO — optional Flask MJPEG server for remote debugging / monitoring.
# Disabled by default. Does not replace native cv2.imshow when HEADLESS=0.
# Requires: pip install flask
# Example: HEADLESS=1 STREAM_VIDEO=1 python run.py
#   then open http://<device-ip>:5000/ in a browser on the LAN.
STREAM_VIDEO = os.environ.get("STREAM_VIDEO", "0") in ("1", "true", "True", "yes")
STREAM_HOST = os.environ.get("STREAM_HOST", "0.0.0.0")
STREAM_PORT = int(os.environ.get("STREAM_PORT", "5000"))
STREAM_JPEG_QUALITY = int(os.environ.get("STREAM_JPEG_QUALITY", "75"))

# CAMERA_BACKEND — selects the frame-acquisition backend:
#   "opencv"               — cv2.VideoCapture(0); laptop / USB webcam (default)
#   "libcamera"            — auto-select GStreamer or subprocess; USE THIS on Pi
#                            with Conda Python to avoid libcamera ABI mismatch
#   "libcamera_gstreamer"  — GStreamer libcamerasrc (needs gst plugins-bad)
#   "libcamera_subprocess" — rpicam-vid subprocess; always works on Pi OS
#   "picamera2"            — Picamera2 (only works with matching Python ABI)
#   "v4l2"                 — explicit V4L2 + MJPEG
CAMERA_BACKEND = os.environ.get("CAMERA_BACKEND", "opencv")

# =====================================================================
# SD-card I/O coalescing (Pi deployment)
# =====================================================================
# Open CSV log files with a write-buffer of this size (bytes).
# Reduces per-row fsync pressure on the SD card.
LOG_BUFFER_SIZE = 8192

# Flush the write buffer to disk every N frames (~every 2s at 15fps).
# Lower values reduce data loss on power-cut; higher values reduce I/O.
LOG_FLUSH_INTERVAL = 30

# Auto-rotate diagnostic_log.csv when it exceeds this size (MB).
# Keeps the SD card from filling up during long calibration sessions.
DIAG_MAX_SIZE_MB = 50.0

# =====================================================================
# Performance instrumentation (Phase 5)
# =====================================================================
# Rolling FPS window: number of past frame timestamps to average over.
FPS_WINDOW = 30

# System-resource sampling interval: sample CPU%, RAM, and temperature
# every N frames (psutil calls have non-trivial overhead; don't call
# every frame).
PERF_SAMPLE_INTERVAL = 10

# =====================================================================
# Research telemetry (frame-level CSV + optional corner overlay)
# =====================================================================
# TELEMETRY=0 disables frame telemetry CSV and the telemetry strip overlay.
# TELEMETRY_LOG_EVERY_N>1 subsamples rows (reduces SD-card writes).
# TELEMETRY_DT_WINDOW — rolling window size for mean/std of frame intervals (ms).
TELEMETRY = _env_truthy("TELEMETRY", "1")
TELEMETRY_OVERLAY = _env_truthy("TELEMETRY_OVERLAY", "1")
TELEMETRY_LOG_EVERY_N = max(1, int(os.environ.get("TELEMETRY_LOG_EVERY_N", "1")))
TELEMETRY_DT_WINDOW = max(2, int(os.environ.get("TELEMETRY_DT_WINDOW", "30")))

# =====================================================================
# Debug frame capture (optional JPEG dumps; rate-limited)
# =====================================================================
# DEBUG_FRAMES=1 enables event-triggered saves under debug_frames/ (or DEBUG_FRAMES_DIR).
# DEBUG_FRAMES_MIN_INTERVAL_S — minimum wall time between any two saves.
# DEBUG_SAMPLE_EVERY_N — if >0, also save one frame every N frames under sampled/.
# DEBUG_YUNET_SCORE_TH — if >0, save when matched face YuNet score is below this.
DEBUG_FRAMES = _env_truthy("DEBUG_FRAMES", "0")
DEBUG_FRAMES_DIR = os.environ.get("DEBUG_FRAMES_DIR", "")
DEBUG_FRAMES_MIN_INTERVAL_S = float(os.environ.get("DEBUG_FRAMES_MIN_INTERVAL_S", "2.0"))
DEBUG_FRAMES_MAX_PER_RUN = max(1, int(os.environ.get("DEBUG_FRAMES_MAX_PER_RUN", "500")))
DEBUG_SAMPLE_EVERY_N = int(os.environ.get("DEBUG_SAMPLE_EVERY_N", "0"))
DEBUG_YUNET_SCORE_TH = float(os.environ.get("DEBUG_YUNET_SCORE_TH", "0"))
DEBUG_JPEG_QUALITY = int(os.environ.get("DEBUG_JPEG_QUALITY", "88"))