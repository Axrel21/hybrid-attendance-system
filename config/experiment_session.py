# config/experiment_session.py
"""
Per-run experiment session layout under experiments/exp_<timestamp>/.

Call init_experiment_session(project_root) once at process startup (run.py or
edge.main __main__). Paths are available via get_current_paths(); the active
root is also in os.environ["EXPERIMENT_ROOT"].
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

_CURRENT: Optional["ExperimentPaths"] = None

# Keys pulled from config.settings for settings_snapshot.json (best-effort).
_SETTINGS_SNAPSHOT_KEYS = (
    "SIMULATE_PI",
    "PI_MAX_THREADS",
    "TARGET_LATENCY_MS",
    "CAMERA_MODE",
    "MATCH_HIGH_BASE",
    "MATCH_MID_BASE",
    "K_FOCAL",
    "MIN_DISTANCE",
    "MAX_DISTANCE",
    "LIVENESS_WINDOW",
    "RIGID_ANGLE_VAR_TH",
    "RIGID_MAG_VAR_TH",
    "SCREEN_LAPLACIAN_TH",
    "STATIC_AREA_VAR_TH",
    "UNREAL_AREA_VAR_TH",
    "MIN_SKIN_RATIO",
    "MAX_BRIGHTNESS_TH",
    "MOTION_MIN_THRESHOLD",
    "ORIENTATION_OVERHEAD_TH",
    "ORIENTATION_TILTED_TH",
    "ORIENTATION_SMOOTHING_WINDOW",
    "POSE_TELEMETRY_MIN_IOU",
    "EXPERIMENT_LABEL",
    "VERBOSE_DEBUG",
    "HEADLESS",
    "STREAM_VIDEO",
    "STREAM_HOST",
    "STREAM_PORT",
    "STREAM_JPEG_QUALITY",
    "CAMERA_BACKEND",
    "LOG_BUFFER_SIZE",
    "LOG_FLUSH_INTERVAL",
    "DIAG_MAX_SIZE_MB",
    "FPS_WINDOW",
    "PERF_SAMPLE_INTERVAL",
    "THERMAL_WARN_C",
    "THERMAL_WARN_INTERVAL_S",
    "TELEMETRY",
    "TELEMETRY_OVERLAY",
    "TELEMETRY_LOG_EVERY_N",
    "TELEMETRY_DT_WINDOW",
    "DEBUG_FRAMES",
    "DEBUG_FRAMES_DIR",
    "DEBUG_FRAMES_MIN_INTERVAL_S",
    "DEBUG_FRAMES_MAX_PER_RUN",
    "DEBUG_SAMPLE_EVERY_N",
    "DEBUG_YUNET_SCORE_TH",
    "DEBUG_JPEG_QUALITY",
    "AUTO_EXPERIMENT_REPORT",
    # Pass-9 minimal runtime stabilization knobs (defaults preserve the
    # historic behaviour). Captured here so a session's snapshot pins
    # exactly which stabilizers were active during the run.
    "YUNET_INPUT_W",
    "YUNET_INPUT_H",
    "BBOX_EMA_ALPHA",
    "SIM_EMA_ALPHA",
    "MATCH_PERSISTENCE_FRAMES",
    "PAD_SPOOF_STREAK_REQUIRED",
)
@dataclass
class ExperimentPaths:
    experiment_id: str
    root: str
    telemetry_dir: str
    diagnostics_dir: str
    debug_frames_dir: str
    plots_dir: str
    logs_dir: str
    config_dir: str
    summaries_dir: str
    telemetry_csv: str
    diagnostic_csv: str
    attendance_csv: str
    settings_snapshot_path: str
    runtime_log_path: str
    debug_log_path: str


def get_current_paths() -> Optional[ExperimentPaths]:
    return _CURRENT


def _json_safe_value(v: Any) -> Any:
    if isinstance(v, (bool, int, float, str, type(None))):
        return v
    return repr(v)


def _write_settings_snapshot(path: str, experiment_id: str) -> None:
    import config.settings as settings_mod

    snapshot: Dict[str, Any] = {
        "experiment_id": experiment_id,
        "settings_module": {},
    }
    for key in _SETTINGS_SNAPSHOT_KEYS:
        if hasattr(settings_mod, key):
            snapshot["settings_module"][key] = _json_safe_value(
                getattr(settings_mod, key)
            )

    runtime_flags = {
        "HEADLESS": os.environ.get("HEADLESS"),
        "SIMULATE_PI": os.environ.get("SIMULATE_PI"),
        "CAMERA_BACKEND": os.environ.get("CAMERA_BACKEND"),
        "EXPERIMENT_LABEL": os.environ.get("EXPERIMENT_LABEL"),
        "TELEMETRY": os.environ.get("TELEMETRY"),
        "TELEMETRY_OVERLAY": os.environ.get("TELEMETRY_OVERLAY"),
        "DEBUG_FRAMES": os.environ.get("DEBUG_FRAMES"),
        "STREAM_VIDEO": os.environ.get("STREAM_VIDEO"),
        "VERBOSE_DEBUG": os.environ.get("VERBOSE_DEBUG"),
    }
    snapshot["runtime_env_overrides"] = {
        k: v for k, v in runtime_flags.items() if v is not None
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)


def init_experiment_session(project_root: str) -> ExperimentPaths:
    """
    Create experiments/exp_<timestamp>/ with subdirs, write settings snapshot,
    set EXPERIMENT_ROOT / EXPERIMENT_ID in the environment.
    """
    global _CURRENT
    ts = time.strftime("%Y%m%d_%H%M%S")
    experiment_id = f"exp_{ts}"
    root = os.path.join(project_root, "experiments", experiment_id)

    telemetry_dir = os.path.join(root, "telemetry")
    diagnostics_dir = os.path.join(root, "diagnostics")
    debug_frames_dir = os.path.join(root, "debug_frames")
    plots_dir = os.path.join(root, "plots")
    logs_dir = os.path.join(root, "logs")
    config_dir = os.path.join(root, "config")
    summaries_dir = os.path.join(root, "summaries")

    for d in (
        telemetry_dir,
        diagnostics_dir,
        debug_frames_dir,
        plots_dir,
        logs_dir,
        config_dir,
        summaries_dir,
    ):
        os.makedirs(d, exist_ok=True)

    telemetry_csv = os.path.join(telemetry_dir, "telemetry_log.csv")
    diagnostic_csv = os.path.join(diagnostics_dir, "diagnostic_log.csv")
    attendance_csv = os.path.join(diagnostics_dir, "attendance_log.csv")
    settings_snapshot_path = os.path.join(config_dir, "settings_snapshot.json")
    runtime_log_path = os.path.join(logs_dir, "runtime.log")
    debug_log_path = os.path.join(logs_dir, "debug.log")

    paths = ExperimentPaths(
        experiment_id=experiment_id,
        root=root,
        telemetry_dir=telemetry_dir,
        diagnostics_dir=diagnostics_dir,
        debug_frames_dir=debug_frames_dir,
        plots_dir=plots_dir,
        logs_dir=logs_dir,
        config_dir=config_dir,
        summaries_dir=summaries_dir,
        telemetry_csv=telemetry_csv,
        diagnostic_csv=diagnostic_csv,
        attendance_csv=attendance_csv,
        settings_snapshot_path=settings_snapshot_path,
        runtime_log_path=runtime_log_path,
        debug_log_path=debug_log_path,
    )

    _write_settings_snapshot(settings_snapshot_path, experiment_id)
    _append_session_index(project_root, paths)

    _CURRENT = paths
    os.environ["EXPERIMENT_ROOT"] = root
    os.environ["EXPERIMENT_ID"] = experiment_id

    return paths


def _append_session_index(project_root: str, paths: "ExperimentPaths") -> None:
    """Best-effort append of one JSONL row per session to experiments/index.jsonl.

    Dashboard-readable enumeration of every run. Wrapped in a blanket except
    so the pipeline never fails because of an index-write hiccup (full disk,
    permission issue on a read-only mount, etc.). Telemetry CSVs remain the
    authoritative per-run record; this file is a convenience index only.
    """
    try:
        index_path = os.path.join(project_root, "experiments", "index.jsonl")
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        record = {
            "experiment_id": paths.experiment_id,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "root": os.path.relpath(paths.root, project_root),
            "telemetry_csv": os.path.relpath(paths.telemetry_csv, project_root),
            "diagnostic_csv": os.path.relpath(paths.diagnostic_csv, project_root),
            "attendance_csv": os.path.relpath(paths.attendance_csv, project_root),
            "experiment_label": os.environ.get("EXPERIMENT_LABEL", ""),
        }
        with open(index_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass
