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
K_FOCAL = 1000
MIN_DISTANCE = 1.0       
MAX_DISTANCE = 3.5       

# 5-Tier Liveness Tuning
LIVENESS_WINDOW = 8
RIGID_ANGLE_VAR_TH = 0.15
RIGID_MAG_VAR_TH = 1.5
SCREEN_LAPLACIAN_TH = 80.0
STATIC_AREA_VAR_TH = 50.0      # Reject photo (no geometric expansion)
UNREAL_AREA_VAR_TH = 5000.0    # Reject glitchy tracking jumps
MIN_SKIN_RATIO = 0.15          # Reject screens/masks with low skin

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
MOTION_MIN_THRESHOLD = 0.5  # Minimum average pixel movement to be considered active motion