# config/settings.py
import os

try:
    import cv2
    cv2.setNumThreads(1)
except ImportError:
    pass

try:
    import tensorflow as tf
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)
except Exception:
    pass

# System Modes & Simulation
SIMULATE_PI = True       
PI_MAX_THREADS = 1       # Restrict OpenCV/TFLite to 1 core for true simulation
TARGET_LATENCY_MS = 65   # Target edge delay (for simulation sync)
CAMERA_MODE = "tilted"   # "tilted" (overhead) or "flat" (frontal)

# Base Matching Thresholds (Adaptive logic will modify these)
MATCH_HIGH_BASE = 0.80
MATCH_MID_BASE = 0.65

# Distance Filtering (Meters)
K_FOCAL = 1000
MIN_DISTANCE = 1.0       
MAX_DISTANCE = 3.5       

# ==========================================
# 🛡️ 5-Tier Liveness & Anti-Spoofing Tuning
# ==========================================
LIVENESS_WINDOW = 8

# Motion & Planar Traps (Defeats Phones / Screens)
MOTION_MIN_THRESHOLD = 0.5    # Minimum average pixel movement to be considered active motion
RIGID_ANGLE_VAR_TH = 0.15     # If all points move same direction, it's a flat surface
RIGID_MAG_VAR_TH = 1.5        # If all points move same speed, it's a flat surface

# Geometry & Texture Traps (Defeats Photos / Masks)
MIN_AREA_VAR_TH = 50.0        # Real faces naturally change size; printed/phones stay rigidly static
MAX_BRIGHTNESS_TH = 200.0     # Phone screens held close often blow out the brightness
SCREEN_LAPLACIAN_TH = 100.0   # Phone pixels often create artificially sharp edges
MIN_SKIN_RATIO = 0.15         # Reject screens/masks with abnormally low human skin tones

# Set Environment Variables for Simulation
if SIMULATE_PI:
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["TF_NUM_INTRAOP_THREADS"] = "1"
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    
    cv2.setNumThreads(1)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)