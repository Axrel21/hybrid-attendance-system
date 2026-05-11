# edge/main.py
"""
FinalHybridEdge — main runtime loop for the attendance edge pipeline.

Pi deployment changes applied in this revision
-----------------------------------------------
B1  TFLite runtime shim (tflite-runtime on ARM, tensorflow on x86)
B2  Camera abstraction via edge.camera (Picamera2 or OpenCV backend)
B3  HEADLESS flag — skip all display calls for SSH / systemd deployments
B4  SIMULATE_PI flag no longer needed to gate thread settings; real Pi
    sets PI_MAX_THREADS=2, SIMULATE_PI=False

Bottleneck fixes (Phase 4)
--------------------------
P2  liveness.assess_frame() now accepts curr_gray; no redundant BGR->Gray
P3  yunet setter calls moved from loop to __init__ (constant thresholds)
P4  stored embeddings pre-normalised in PipelineController.__init__
P5  cv2.namedWindow/resizeWindow moved to __init__ (not every frame)
P6  log files opened with 8 KB write buffer; flushed every N frames
    (minor) duplicate h,w=frame.shape[:2] removed
    (minor) DATA_DIR import from enrollment.enroll removed (unused)

Performance instrumentation (Phase 5)
--------------------------------------
    per-stage timing (t_detect, t_liveness, t_embed, t_match)
    rolling FPS counter
    CPU%, RAM MB, CPU temperature columns in diagnostic log
"""

import csv
import json
import os
import signal
import time
from collections import deque

import cv2
import numpy as np

# -----------------------------------------------------------------
# TFLite runtime shim (B1)
# Use the lightweight tflite-runtime on Pi (ARM64, no full TF wheel);
# fall back to the tensorflow package on the x86 dev machine.
# -----------------------------------------------------------------
try:
    from tflite_runtime.interpreter import Interpreter as TFLiteInterpreter
except ImportError:
    import tensorflow as tf          # noqa: F401
    TFLiteInterpreter = tf.lite.Interpreter

# -----------------------------------------------------------------
# Optional psutil for system-resource sampling (Phase 5)
# -----------------------------------------------------------------
try:
    import psutil as _psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _psutil = None
    _PSUTIL_AVAILABLE = False

from config import experiment_session, settings
from config.logging_setup import LOG_DEBUG, LOG_RUNTIME, ensure_session_logging
from edge import telemetry
from edge.camera import CameraSource
from edge.tracker import HybridTracker
from edge.liveness import LivenessEngine
from edge.align import align_face
from edge.orientation import PoseEstimator
from edge.pipeline_controller import PipelineController
from edge.utils import is_valid_face

# -----------------------------------------------------------------
# Absolute paths — derived from this file's location.
# Removed the fragile `from enrollment.enroll import DATA_DIR` import
# (the symbol was unused here; DATA_DIR is only needed by enroll.py).
# -----------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
model_path1 = os.path.join(_PROJECT_ROOT, "models", "yunet.onnx")
model_path2 = os.path.join(_PROJECT_ROOT, "models", "mobilefacenet.tflite")
data_path   = os.path.join(_PROJECT_ROOT, "data",   "known_faces.json")


def _artifact_paths():
    """Resolve CSV/debug paths for the active experiment session (or create one)."""
    p = experiment_session.get_current_paths()
    if p is None:
        p = experiment_session.init_experiment_session(_PROJECT_ROOT)
    return p


# =================================================================
# Helper: CPU temperature (Pi sysfs; 0.0 on non-Pi / non-Linux)
# =================================================================
def _read_cpu_temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return 0.0


# =================================================================
# Drawing helpers
# =================================================================
def draw_debug_info(frame, x, y, info_lines, color):
    y_offset = y
    for line in info_lines:
        cv2.putText(frame, line, (x, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 2)
        cv2.putText(frame, line, (x, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        y_offset += 15


# =================================================================
# IoU / NMS helpers
# =================================================================
def calculate_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0]); yA = max(boxA[1], boxB[1])
    xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
    yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea  = boxA[2] * boxA[3]
    boxBArea  = boxB[2] * boxB[3]
    return interArea / float(boxAArea + boxBArea - interArea + 1e-5)


def suppress_overlapping(faces, iou_th=0.45, iomin_th=0.70):
    """
    Greedy post-pass over YuNet output.

    Standard IoU-NMS (which YuNet already runs internally at 0.30) does not
    catch the nested-box case: when a small box is fully inside a large one,
    IoU = area(small) / area(large) can sit well below the NMS threshold.
    Phone-replay frames routinely produce such pairs.

    Adds IoMin = intersection / min(area_A, area_B) which is ~1.0 for any
    nested pair regardless of the size ratio.
    """
    if faces is None or len(faces) == 0:
        return faces

    scores = faces[:, -1] if faces.shape[1] >= 15 else np.ones(len(faces))
    order = np.argsort(-scores)
    kept = []
    for idx in order:
        f = faces[idx]
        bx, by, bw, bh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
        b_area = max(1.0, bw * bh)
        suppressed = False
        for kf in kept:
            kx, ky, kw, kh = float(kf[0]), float(kf[1]), float(kf[2]), float(kf[3])
            k_area = max(1.0, kw * kh)
            ix1 = max(bx, kx); iy1 = max(by, ky)
            ix2 = min(bx + bw, kx + kw); iy2 = min(by + bh, ky + kh)
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            iou   = inter / (b_area + k_area - inter + 1e-6)
            iomin = inter / min(b_area, k_area)
            if iou > iou_th or iomin > iomin_th:
                suppressed = True
                break
        if not suppressed:
            kept.append(f)
    return np.array(kept) if kept else None


def find_best_face_match(tracker_box, detected_faces, iou_threshold=0.3):
    best_match = None
    max_iou    = -1.0
    if detected_faces is None:
        return None
    for face in detected_faces:
        face_box = (int(face[0]), int(face[1]), int(face[2]), int(face[3]))
        iou = calculate_iou(tracker_box, face_box)
        if iou > max_iou:
            max_iou    = iou
            best_match = face
    return best_match if max_iou >= iou_threshold else None


# =================================================================
# Diagnostic CSV schema — single source of truth
# =================================================================
# Column order MUST remain stable across runs. Legacy columns (through
# latency_ms) are preserved verbatim. Orientation calibration columns
# were added in the instrumentation phase. Performance instrumentation
# columns (t_detect_ms … cpu_temp_c) are added in this Pi deployment phase.
#
# On first run after upgrading from an older schema the existing
# diagnostic_log.csv in this session directory is auto-rotated to
# diagnostic_log.archived_<ts>.csv so schemas never mix silently.
DIAG_COLUMNS = [
    # --- legacy block (DO NOT reorder — downstream consumers depend on it) ---
    "timestamp",  "frame_w",    "frame_h",    "track_id",
    "lbl",        "live_conf",  "reason",     "decision",
    "mode",       "distance",   "brightness",
    "avg_mag",    "avg_ang_var","avg_mag_var","avg_area_var","rigid_ratio",
    "m_score",    "g_score",
    "identity",   "sim",        "th_high",    "th_mid",
    "latency_ms",
    # --- orientation calibration block ---
    "face_w",     "face_h",
    "mode_raw",   "orient_ratio","eye_dist_px","vertical_dist_px",
    # --- recognition pool tracing ---
    "pool_used",  "pool_size",  "num_identities",
    # --- session tag ---
    "experiment_label",
    # --- performance instrumentation (Pi deployment) ---
    "t_detect_ms","t_liveness_ms","t_embed_ms","t_match_ms",
    "fps_rolling",
    "cpu_pct",    "mem_mb",     "cpu_temp_c",
]


def _rotate_diag_if_schema_changed(path, expected_columns):
    """
    Auto-rotate the diagnostic CSV when its header doesn't match.
    Returns True  -> caller should open a fresh file and write the header.
    Returns False -> existing file header matches; caller should append.
    """
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return True
    try:
        with open(path, "r", newline="") as f:
            header = next(csv.reader(f), [])
    except Exception:
        ts = time.strftime("%Y%m%d_%H%M%S")
        os.rename(path, path.replace(".csv", f".unreadable_{ts}.csv"))
        return True
    if header == expected_columns:
        return False
    ts       = time.strftime("%Y%m%d_%H%M%S")
    archived = path.replace(".csv", f".archived_{ts}.csv")
    os.rename(path, archived)
    LOG_RUNTIME.info(
        "Diagnostic schema changed; archived previous log to %s",
        os.path.basename(archived),
    )
    return True


# =================================================================
# Main pipeline class
# =================================================================
class FinalHybridEdge:
    def __init__(self):
        ensure_session_logging(settings.VERBOSE_DEBUG)

        # ---- Thread configuration ----
        if settings.SIMULATE_PI:
            cv2.setNumThreads(settings.PI_MAX_THREADS)

        # ---- YuNet (640x480 fixed resolution) ----
        self.yunet = cv2.FaceDetectorYN.create(model_path1, "", (640, 480), 0.8, 0.3, 5000)
        # P3 fix: move constant setter calls out of the hot loop.
        # Resolution is fixed at 640x480; thresholds never change at runtime.
        self.yunet.setInputSize((640, 480))
        self.yunet.setScoreThreshold(0.50)
        self.yunet.setNMSThreshold(0.30)

        # ---- MobileFaceNet TFLite ----
        self.interpreter = TFLiteInterpreter(model_path=model_path2)
        if settings.SIMULATE_PI and hasattr(self.interpreter, "set_num_threads"):
            self.interpreter.set_num_threads(settings.PI_MAX_THREADS)
        self.interpreter.allocate_tensors()
        self.in_idx  = self.interpreter.get_input_details()[0]["index"]
        self.out_idx = self.interpreter.get_output_details()[0]["index"]

        # ---- Sub-modules ----
        self.tracker   = HybridTracker()
        self.liveness  = LivenessEngine()
        self.pose_est  = PoseEstimator()

        with open(data_path, "r") as f:
            db = json.load(f)
        self.controller = PipelineController(db)

        self.embedding_buffers: dict = {}
        self.cooldowns: dict = {}

        paths = _artifact_paths()
        self._experiment_paths = paths

        # ---- Attendance log (matched events only; lean on SD card) ----
        log_path = paths.attendance_csv
        log_exists = os.path.isfile(log_path) and os.path.getsize(log_path) > 0
        # P6 fix: open with 8 KB write buffer to reduce SD-card I/O pressure.
        self.log_file   = open(log_path, "a", newline="", buffering=settings.LOG_BUFFER_SIZE)
        self.csv_writer = csv.writer(self.log_file)
        if not log_exists:
            self.csv_writer.writerow([
                "name", "confidence", "timestamp", "latency",
                "liveness_label", "reason", "distance", "brightness",
                "motion_score", "geometry_score", "mode", "track_id",
            ])

        # ---- Per-frame diagnostic log ----
        diag_path = paths.diagnostic_csv
        write_header   = _rotate_diag_if_schema_changed(diag_path, DIAG_COLUMNS)
        self.diag_file   = open(diag_path, "a", newline="", buffering=settings.LOG_BUFFER_SIZE)
        self.diag_writer = csv.writer(self.diag_file)
        if write_header:
            self.diag_writer.writerow(DIAG_COLUMNS)

        if settings.EXPERIMENT_LABEL:
            LOG_RUNTIME.info(
                "Tagging session EXPERIMENT_LABEL=%r",
                settings.EXPERIMENT_LABEL,
            )

        # ---- Performance instrumentation state ----
        self._frame_count  = 0
        self._last_thermal_warn = 0.0
        self._fps_times    = deque(maxlen=settings.FPS_WINDOW)
        self._cpu_pct      = 0.0
        self._mem_mb       = 0.0
        self._cpu_temp_c   = 0.0
        if _PSUTIL_AVAILABLE:
            self._psutil_proc = _psutil.Process()

        # Overlays on frame: native window and/or MJPEG viewers need annotations.
        self._show_overlay = (not settings.HEADLESS) or settings.STREAM_VIDEO

        # ---- Frame telemetry (CSV + optional HUD strip) ----
        self._telemetry_state = None
        self._telemetry_file = None
        self._telemetry_writer = None
        self._telemetry_show_strip = False
        if settings.TELEMETRY:
            self._telemetry_state = telemetry.TelemetryFrameState(settings.TELEMETRY_DT_WINDOW)
            telemetry_path = paths.telemetry_csv
            _tel_hdr = telemetry.rotate_if_schema_changed(
                telemetry_path, telemetry.TELEMETRY_CSV_COLUMNS
            )
            self._telemetry_file = open(
                telemetry_path, "a", newline="", buffering=settings.LOG_BUFFER_SIZE
            )
            self._telemetry_writer = csv.writer(self._telemetry_file)
            if _tel_hdr:
                self._telemetry_writer.writerow(telemetry.TELEMETRY_CSV_COLUMNS)
            self._telemetry_show_strip = settings.TELEMETRY_OVERLAY and self._show_overlay

        # ---- Debug JPEG capture (event-triggered) ----
        self._debug_writer = None
        if settings.DEBUG_FRAMES:
            _dbg_root = settings.DEBUG_FRAMES_DIR or paths.debug_frames_dir
            self._debug_writer = telemetry.DebugFrameWriter(
                _dbg_root,
                settings.DEBUG_FRAMES_MIN_INTERVAL_S,
                settings.DEBUG_FRAMES_MAX_PER_RUN,
                settings.DEBUG_JPEG_QUALITY,
            )

        # Optional MJPEG (secondary to cv2.imshow; off by default).
        self._stream_set_frame = None
        if settings.STREAM_VIDEO:
            try:
                from edge.stream_server import set_frame, start_stream_server
            except ImportError as exc:
                raise ImportError(
                    "STREAM_VIDEO=1 requires Flask. Install with: pip install flask"
                ) from exc
            self._stream_set_frame = set_frame
            start_stream_server(
                host=settings.STREAM_HOST,
                port=settings.STREAM_PORT,
                daemon=True,
            )
            LOG_RUNTIME.info(
                "MJPEG stream http://%s:%s/video_feed",
                settings.STREAM_HOST,
                settings.STREAM_PORT,
            )

        # ---- Display window — create once (P5 fix) ----
        # B3: skip entirely in HEADLESS mode (no display available).
        if not settings.HEADLESS:
            from edge import opencv_highgui

            if not opencv_highgui.skip_gui_precheck():
                _hg_ok, _hg_detail = opencv_highgui.check_highgui_from_build_info()
                if not _hg_ok:
                    raise RuntimeError(
                        f"{_hg_detail}\n"
                        "See deployment/OPENCV_GUI_RASPBERRY_PI.md "
                        "or set SKIP_OPENCV_GUI_CHECK=1 to bypass this check."
                    )
            try:
                cv2.namedWindow("Hybrid Edge Pipeline", cv2.WINDOW_NORMAL)
                cv2.resizeWindow("Hybrid Edge Pipeline", 720, 540)
            except cv2.error as exc:
                raise RuntimeError(
                    "Failed to create OpenCV HighGUI window (HEADLESS=0). "
                    "Install opencv-python with GTK support, not "
                    "opencv-python-headless. See deployment/OPENCV_GUI_RASPBERRY_PI.md"
                ) from exc

    # ------------------------------------------------------------------
    def extract_embedding(self, face_img):
        inp = (np.float32(face_img) - 127.5) / 128.0
        inp = np.expand_dims(inp, axis=0)
        self.interpreter.set_tensor(self.in_idx, inp)
        self.interpreter.invoke()
        emb = self.interpreter.get_tensor(self.out_idx)[0]
        return emb / np.linalg.norm(emb)

    # ------------------------------------------------------------------
    def _draw_overlay(self, frame, x, y, fw, fh, track_id, dbg):
        """Draw per-track debug overlay. Caller must gate on ``_show_overlay``."""
        if dbg["decision"] == "NO_MATCH":
            return

        lbl      = dbg["lbl"]
        decision = dbg["decision"]
        if decision in ("MATCHED", "OFFLOAD_TO_CLOUD"):
            color = (0, 255, 0)
        elif decision in ("REJECTED_LIVENESS", "OUT_OF_RANGE") or lbl == "SPOOF":
            color = (0, 0, 255)
        else:
            color = (180, 180, 180)

        info_lines = [
            f"ID:{track_id} {dbg['mode']} d:{dbg['distance']:.2f}m  FPS:{dbg['fps_rolling']:.1f}",
            f"Live:{lbl} ({dbg['live_conf']:.2f})  br:{dbg['brightness']:.0f}"
            f"  T:{dbg['cpu_temp_c']:.0f}C",
            f"sim:{dbg['sim']:.2f}/th:{dbg['th_high']:.2f}  {dbg['identity']}",
            f"mag:{dbg['avg_mag']:.2f}  angV:{dbg['avg_ang_var']:.3f}"
            f"  magV:{dbg['avg_mag_var']:.2f}",
            f"rigid:{dbg['rigid_ratio']:.2f}  areaV:{dbg['avg_area_var']:.0f}",
            f"D:{decision}  det:{dbg['t_detect_ms']:.0f}ms"
            f"  emb:{dbg['t_embed_ms']:.0f}ms",
            f"R:{dbg['rsn']}",
        ]
        cv2.rectangle(frame, (x, y), (x + fw, y + fh), color, 2)
        draw_debug_info(frame, x + fw + 8, max(20, y), info_lines, color)

    # ------------------------------------------------------------------
    def _write_diag(self, loop_start, frame_w, frame_h, track_id, dbg):
        """Append one diagnostic row per (frame, track), regardless of decision.

        Column order MUST match DIAG_COLUMNS exactly.
        """
        latency_ms = (time.time() - loop_start) * 1000.0
        self.diag_writer.writerow([
            # legacy block
            round(time.time(), 3),  frame_w,           frame_h,           track_id,
            dbg["lbl"],             round(dbg["live_conf"],   3), dbg["rsn"], dbg["decision"],
            dbg["mode"],            round(dbg["distance"],    3), round(dbg["brightness"],  1),
            round(dbg["avg_mag"],   3), round(dbg["avg_ang_var"], 4),
            round(dbg["avg_mag_var"], 3), round(dbg["avg_area_var"], 1),
            round(dbg["rigid_ratio"], 3),
            round(dbg["m_score"],   3), round(dbg["g_score"],     3),
            dbg["identity"],        round(dbg["sim"],      3),
            round(dbg["th_high"],   3), round(dbg["th_mid"],      3),
            round(latency_ms, 1),
            # orientation calibration block
            int(dbg["face_w"]), int(dbg["face_h"]),
            dbg["mode_raw"],        round(dbg["orient_ratio"],      4),
            round(dbg["eye_dist_px"],     2), round(dbg["vertical_dist_px"],  2),
            # recognition pool tracing
            dbg["pool_used"],       int(dbg["pool_size"]),  int(dbg["num_identities"]),
            # session tag
            settings.EXPERIMENT_LABEL,
            # performance instrumentation block
            round(dbg["t_detect_ms"],   2), round(dbg["t_liveness_ms"], 2),
            round(dbg["t_embed_ms"],    2), round(dbg["t_match_ms"],    2),
            round(dbg["fps_rolling"],   2),
            round(dbg["cpu_pct"],  1), round(dbg["mem_mb"],      1),
            round(dbg["cpu_temp_c"], 1),
        ])

    # ------------------------------------------------------------------
    def run(self):
        # ---- Clean-shutdown signal handler (systemd SIGTERM, Ctrl-C) ----
        _shutdown = [False]

        def _handle_signal(sig, _frame):
            _shutdown[0] = True

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT,  _handle_signal)

        # ---- Camera (B2 fix: abstracted backend) ----
        # fps is used by libcamera backends; OpenCV negotiates fps itself.
        cap = CameraSource(settings.CAMERA_BACKEND, width=640, height=480, fps=15)
        prev_gray = None

        while not _shutdown[0]:
            loop_start = time.time()
            loop_start_pc = time.perf_counter()
            frame_idx = 0
            dt_ms = mean_dt = std_dt = 0.0
            if self._telemetry_state is not None:
                frame_idx, dt_ms, mean_dt, std_dt = self._telemetry_state.tick_dt(loop_start_pc)

            t0 = time.perf_counter()
            ret, frame = cap.read()
            t_capture_ms = (time.perf_counter() - t0) * 1000.0
            if not ret:
                break

            # One authoritative h, w per frame (duplicate removed — minor fix)
            h, w = frame.shape[:2]
            curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # ---- YuNet detection (setters are in __init__ now) ----
            t0 = time.perf_counter()
            _, faces = self.yunet.detect(frame)
            t_detect_ms = (time.perf_counter() - t0) * 1000.0

            raw_count  = 0 if faces is None else len(faces)
            faces      = suppress_overlapping(faces)
            kept_count = 0 if faces is None else len(faces)

            if settings.VERBOSE_DEBUG:
                LOG_DEBUG.debug(
                    "Frame %dx%d faces:%d (raw %d) det:%.1fms",
                    w,
                    h,
                    kept_count,
                    raw_count,
                    t_detect_ms,
                )

            # ---- Face validation ----
            rects       = []
            valid_faces = []
            if faces is not None:
                for f in faces:
                    rx, ry, rw, rh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
                    crop          = frame[max(0, ry):ry + rh, max(0, rx):rx + rw]
                    raw_landmarks = [(int(f[4 + 2*j]), int(f[4 + 2*j + 1])) for j in range(5)]
                    if not is_valid_face(crop, raw_landmarks, (rx, ry, rw, rh), w, h):
                        continue
                    rects.append((rx, ry, rw, rh))
                    valid_faces.append(f)

            objects = self.tracker.update(rects)

            # Automatic debug JPEGs only when something actionable is on-frame:
            # pipeline-validated face(s) and/or tracker output (manual 's' unaffected).
            _debug_auto_save_ok = len(valid_faces) > 0 or len(objects) > 0

            # ---- Rolling FPS ----
            self._fps_times.append(loop_start)
            fps_rolling = (
                (len(self._fps_times) - 1) /
                max(1e-9, self._fps_times[-1] - self._fps_times[0])
            ) if len(self._fps_times) >= 2 else 0.0

            # ---- System resource sampling (every PERF_SAMPLE_INTERVAL frames) ----
            self._frame_count += 1
            if _PSUTIL_AVAILABLE and self._frame_count % settings.PERF_SAMPLE_INTERVAL == 0:
                self._cpu_pct    = _psutil.cpu_percent(interval=None)
                self._mem_mb     = self._psutil_proc.memory_info().rss / 1e6
                self._cpu_temp_c = _read_cpu_temp()
                if (
                    settings.THERMAL_WARN_C > 0
                    and self._cpu_temp_c >= settings.THERMAL_WARN_C
                    and (time.time() - self._last_thermal_warn)
                    >= settings.THERMAL_WARN_INTERVAL_S
                ):
                    LOG_RUNTIME.warning(
                        "CPU temperature %.1f C >= threshold %.1f C",
                        self._cpu_temp_c,
                        settings.THERMAL_WARN_C,
                    )
                    self._last_thermal_warn = time.time()

            # ---- Per-track pipeline ----
            t_tracks_start = time.perf_counter()
            max_tl = max_te = max_tm = 0.0
            max_live = max_sim = 0.0
            overlay_draw_ms = 0.0
            for track_id, (centroid, box) in objects.items():
                x, y, fw, fh = box

                dbg = {
                    "lbl": "NA",  "live_conf": 0.0, "rsn": "init",
                    "mode": "NA", "distance": 0.0,  "brightness": 0.0,
                    "m_score": 0.0, "g_score": 0.0,
                    "avg_mag": 0.0, "avg_ang_var": 0.0, "avg_mag_var": 0.0,
                    "avg_area_var": 0.0, "rigid_ratio": 0.0,
                    "sim": 0.0, "identity": "NA",
                    "th_high": 0.0, "th_mid": 0.0,
                    "decision": "NONE",
                    # orientation calibration fields
                    "face_w": fw, "face_h": fh,
                    "mode_raw": "NA", "orient_ratio": 0.0,
                    "eye_dist_px": 0.0, "vertical_dist_px": 0.0,
                    # recognition pool tracing
                    "pool_used": "NA", "pool_size": 0,
                    "num_identities": len(self.controller.db),
                    # performance instrumentation — detection time is frame-level
                    "t_detect_ms":   t_detect_ms,
                    "t_liveness_ms": 0.0,
                    "t_embed_ms":    0.0,
                    "t_match_ms":    0.0,
                    "fps_rolling":   fps_rolling,
                    "cpu_pct":       self._cpu_pct,
                    "mem_mb":        self._mem_mb,
                    "cpu_temp_c":    self._cpu_temp_c,
                    "_yunet_score":  None,
                }

                try:
                    matched_face = find_best_face_match(box, valid_faces, iou_threshold=0.3)

                    if matched_face is None:
                        self.embedding_buffers.pop(track_id, None)
                        self.liveness.history.pop(track_id, None)
                        self.liveness.last_signals.pop(track_id, None)
                        self.liveness.real_streak.pop(track_id, None)
                        self.liveness.planar_streak.pop(track_id, None)
                        dbg["rsn"]      = "No detection match"
                        dbg["decision"] = "NO_MATCH"
                        continue

                    landmarks = [
                        (int(matched_face[4 + 2*j]), int(matched_face[4 + 2*j + 1]))
                        for j in range(5)
                    ]
                    if matched_face.shape[0] >= 15:
                        dbg["_yunet_score"] = float(matched_face[-1])

                    mode         = self.pose_est.estimate_mode(track_id, landmarks)
                    dbg["mode"]  = mode

                    orient_meta              = self.pose_est.last_metrics.get(track_id, {})
                    dbg["mode_raw"]          = orient_meta.get("mode_raw",      "NA")
                    dbg["orient_ratio"]      = float(orient_meta.get("ratio",       0.0))
                    dbg["eye_dist_px"]       = float(orient_meta.get("eye_dist",    0.0))
                    dbg["vertical_dist_px"]  = float(orient_meta.get("vertical_dist", 0.0))

                    distance         = settings.K_FOCAL / (np.sqrt(fw * fh) + 1e-5)
                    dbg["distance"]  = float(distance)
                    if not (settings.MIN_DISTANCE < distance < settings.MAX_DISTANCE):
                        dbg["rsn"]      = f"Distance OOR ({distance:.2f}m)"
                        dbg["decision"] = "OUT_OF_RANGE"
                        continue

                    # P2 fix: pass curr_gray to avoid redundant BGR->Gray in liveness
                    t0 = time.perf_counter()
                    lbl, conf, rsn, m_score, g_score = self.liveness.assess_frame(
                        track_id, mode, prev_gray, curr_gray, frame, box, landmarks)
                    dbg["t_liveness_ms"] = (time.perf_counter() - t0) * 1000.0
                    dbg["lbl"]       = lbl
                    dbg["live_conf"] = float(conf)
                    dbg["rsn"]       = rsn
                    dbg["m_score"]   = float(m_score)
                    dbg["g_score"]   = float(g_score)

                    sig = self.liveness.last_signals.get(track_id, {})
                    dbg["avg_mag"]      = float(sig.get("avg_mag",       0.0))
                    dbg["avg_ang_var"]  = float(sig.get("avg_angle_var", 0.0))
                    dbg["avg_mag_var"]  = float(sig.get("avg_mag_var",   0.0))
                    dbg["avg_area_var"] = float(sig.get("avg_area_var",  0.0))
                    dbg["rigid_ratio"]  = float(sig.get("rigid_ratio",   0.0))

                    if lbl == "SPOOF":
                        self.embedding_buffers.pop(track_id, None)
                        dbg["decision"] = "REJECTED_LIVENESS"
                        continue
                    if lbl != "REAL":
                        dbg["decision"] = lbl
                        continue

                    if track_id not in self.embedding_buffers:
                        self.embedding_buffers[track_id] = deque(maxlen=settings.LIVENESS_WINDOW)

                    # ---- 5-point alignment + embedding ----
                    bgr_crop = frame[max(0, y):y + fh, max(0, x):x + fw]
                    if bgr_crop.size > 0:
                        t0 = time.perf_counter()
                        local_lm    = [(lx - x, ly - y) for lx, ly in landmarks]
                        aligned     = align_face(bgr_crop, local_lm)
                        emb         = self.extract_embedding(aligned)
                        dbg["t_embed_ms"] = (time.perf_counter() - t0) * 1000.0
                        self.embedding_buffers[track_id].append(emb)

                    if len(self.embedding_buffers[track_id]) < settings.LIVENESS_WINDOW:
                        dbg["decision"] = "BUFFERING"
                        try:
                            dbg["brightness"] = float(np.mean(curr_gray[y:y + fh, x:x + fw]))
                        except Exception:
                            pass
                        continue

                    mean_emb = np.mean(self.embedding_buffers[track_id], axis=0)
                    mean_emb = mean_emb / np.linalg.norm(mean_emb)

                    # ---- Pose-aware recognition ----
                    t0 = time.perf_counter()
                    identity, sim = self.controller.pose_aware_match(mean_emb, mode)
                    dbg["t_match_ms"] = (time.perf_counter() - t0) * 1000.0
                    dbg["identity"]   = identity
                    dbg["sim"]        = float(sim)

                    match_meta            = self.controller.last_match_meta or {}
                    dbg["pool_used"]      = match_meta.get("pool_used",      "NA")
                    dbg["pool_size"]      = int(match_meta.get("pool_size",      0))
                    dbg["num_identities"] = int(match_meta.get("num_identities", dbg["num_identities"]))

                    brightness        = np.mean(curr_gray[y:y + fh, x:x + fw])
                    dbg["brightness"] = float(brightness)
                    th_high, th_mid   = self.controller.get_adaptive_threshold(
                        brightness, distance, mode == "OVERHEAD")
                    dbg["th_high"] = float(th_high)
                    dbg["th_mid"]  = float(th_mid)

                    if sim >= th_high:
                        dbg["decision"] = "MATCHED"
                        if time.time() - self.cooldowns.get(identity, 0) > 300:
                            self.cooldowns[identity] = time.time()
                            LOG_RUNTIME.info("Attendance marked: %s", identity)
                    elif sim >= th_mid:
                        dbg["decision"] = "OFFLOAD_TO_CLOUD"
                        if settings.VERBOSE_DEBUG:
                            LOG_DEBUG.debug("Offloading to ArcFace server (sim in mid band)")
                    else:
                        dbg["decision"] = "BELOW_THRESHOLD"

                    total_latency = (time.time() - loop_start) * 1000.0
                    self.csv_writer.writerow([
                        identity,        round(sim, 3),          time.time(),
                        round(total_latency, 1),
                        lbl,             rsn,
                        round(distance, 2), round(brightness, 1),
                        round(m_score, 2),  round(g_score, 2),
                        mode,            track_id,
                    ])

                finally:
                    max_tl = max(max_tl, float(dbg["t_liveness_ms"]))
                    max_te = max(max_te, float(dbg["t_embed_ms"]))
                    max_tm = max(max_tm, float(dbg["t_match_ms"]))
                    max_live = max(max_live, float(dbg["live_conf"]))
                    max_sim = max(max_sim, float(dbg["sim"]))
                    if self._show_overlay:
                        _ov0 = time.perf_counter()
                        self._draw_overlay(frame, x, y, fw, fh, track_id, dbg)
                        overlay_draw_ms += (time.perf_counter() - _ov0) * 1000.0
                    if self._debug_writer is not None and _debug_auto_save_ok:
                        _ys = dbg.get("_yunet_score")
                        for _subdir, _tag, _extra in telemetry.classify_debug_events(
                            track_id,
                            dbg["decision"],
                            dbg["lbl"],
                            _ys,
                            settings.DEBUG_YUNET_SCORE_TH,
                        ):
                            self._debug_writer.save(frame, _subdir, _tag, _extra)
                    self._write_diag(loop_start, w, h, track_id, dbg)

            t_tracks_ms = (time.perf_counter() - t_tracks_start) * 1000.0

            prev_gray = curr_gray.copy()

            tel_strip_ms = 0.0
            if self._telemetry_show_strip:
                ts0 = time.perf_counter()
                lines = [
                    f"FPS:{fps_rolling:.1f}  dt:{mean_dt:.0f}+/-{std_dt:.0f}ms",
                    f"CAP:{t_capture_ms:.0f} DET:{t_detect_ms:.0f} TRK:{t_tracks_ms:.0f}ms",
                    f"LIVE:{max_tl:.0f} EMB:{max_te:.0f} MATCH:{max_tm:.0f}ms",
                    f"OVR:{overlay_draw_ms:.0f}ms  CPU:{self._cpu_pct:.0f}% TEMP:{self._cpu_temp_c:.0f}C",
                    f"RAM:{self._mem_mb:.0f}MB  TRK:{len(objects)} FACE:{len(valid_faces)}",
                    f"SIMmax:{max_sim:.2f}  LIVEmax:{max_live:.2f}",
                ]
                telemetry.draw_telemetry_lines(frame, lines)
                tel_strip_ms = (time.perf_counter() - ts0) * 1000.0
            t_overlay_ms = overlay_draw_ms + tel_strip_ms

            if (
                self._debug_writer is not None
                and settings.DEBUG_SAMPLE_EVERY_N > 0
                and _debug_auto_save_ok
            ):
                if self._frame_count % settings.DEBUG_SAMPLE_EVERY_N == 0:
                    self._debug_writer.save(frame, "sampled", "every_n", str(self._frame_count))

            t_post_start = time.perf_counter()
            if self._stream_set_frame is not None:
                jq = settings.STREAM_JPEG_QUALITY
                self._stream_set_frame(frame, jq)

            if not settings.HEADLESS:
                cv2.imshow("Hybrid Edge Pipeline", frame)
                elapsed_ms = (time.time() - loop_start) * 1000.0
                if settings.SIMULATE_PI:
                    sleep_ms = max(1, int(settings.TARGET_LATENCY_MS - elapsed_ms))
                    key = cv2.waitKey(sleep_ms) & 0xFF
                else:
                    key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s") and self._debug_writer is not None:
                    self._debug_writer.save(frame, "manual", "key_s", str(self._frame_count))
            else:
                # Headless: use time.sleep to respect the target latency budget.
                remaining = settings.TARGET_LATENCY_MS / 1000.0 - (time.time() - loop_start)
                if remaining > 0:
                    time.sleep(remaining)

            t_post_ms = (time.perf_counter() - t_post_start) * 1000.0
            t_total_ms = (time.perf_counter() - loop_start_pc) * 1000.0

            if (
                self._telemetry_writer is not None
                and self._frame_count % settings.TELEMETRY_LOG_EVERY_N == 0
            ):
                self._telemetry_writer.writerow([
                    round(time.time(), 3),
                    frame_idx,
                    settings.EXPERIMENT_LABEL,
                    round(fps_rolling, 2),
                    round(dt_ms, 2),
                    round(std_dt, 2),
                    round(t_capture_ms, 2),
                    round(t_detect_ms, 2),
                    round(t_tracks_ms, 2),
                    round(max_tl, 2),
                    round(max_te, 2),
                    round(max_tm, 2),
                    round(t_overlay_ms, 2),
                    round(t_post_ms, 2),
                    round(t_total_ms, 2),
                    round(self._cpu_pct, 1),
                    round(self._mem_mb, 1),
                    round(self._cpu_temp_c, 1),
                    len(objects),
                    len(valid_faces),
                    raw_count,
                    kept_count,
                    round(max_live, 3),
                    round(max_sim, 3),
                ])

            # ---- P6 fix: periodic CSV flush (SD-card I/O coalescing) ----
            if self._frame_count % settings.LOG_FLUSH_INTERVAL == 0:
                self.log_file.flush()
                self.diag_file.flush()
                if self._telemetry_file is not None:
                    self._telemetry_file.flush()

        # ---- Cleanup ----
        LOG_RUNTIME.info("Pipeline shutdown (closing camera and log files).")
        cap.release()
        if not settings.HEADLESS:
            cv2.destroyAllWindows()
        self.log_file.flush()
        self.log_file.close()
        self.diag_file.flush()
        self.diag_file.close()
        if self._telemetry_file is not None:
            self._telemetry_file.flush()
            self._telemetry_file.close()


if __name__ == "__main__":
    experiment_session.init_experiment_session(_PROJECT_ROOT)
    from config import settings
    from config.logging_setup import configure_session_logging

    _p = experiment_session.get_current_paths()
    if _p is not None:
        configure_session_logging(_p, settings.VERBOSE_DEBUG)
    node = FinalHybridEdge()
    node.run()
