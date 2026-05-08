# config/settings.py
import os

try:
    import cv2
    cv2.setNumThreads(1)
except ImportError:
    pass

# FIX-8: Removed unconditional import and setting of tensorflow threads to prevent bottlenecking non-Pi environments.

# System Modes & Simulation
SIMULATE_PI = True       
PI_MAX_THREADS = 1       # Restrict OpenCV/TFLite to 1 core for true simulation
TARGET_LATENCY_MS = 65   # Target edge delay (for simulation sync)
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
SCREEN_LAPLACIAN_TH = 80

# Set Environment Variables for Simulation
if SIMULATE_PI:
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["TF_NUM_INTRAOP_THREADS"] = "1"
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    cv2.setNumThreads(1)
    # TF optimizations safely nested under SIMULATE_PI condition
    try:
        import tensorflow as tf
        tf.config.threading.set_inter_op_parallelism_threads(1)
        tf.config.threading.set_intra_op_parallelism_threads(1) 
    except Exception:
        pass
    
# Liveness Motion Tuning
MOTION_MIN_THRESHOLD = 0.35    # was 0.5; lets near-still real users qualify as "moving" so they
                               # escape Gate C (which requires NOT is_moving).
                               # NOTE: this also feeds analyze_motion's per-frame is_rigid flag, so it
                               # mildly increases Gate A pressure. Combined with Tier 2, net REAL
                               # acceptance still improves. If you observe rigid_ratio drift upward
                               # for REAL after this change, revert to 0.5.