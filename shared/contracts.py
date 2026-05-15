# shared/contracts.py
"""Edge ↔ Cloud HTTP wire-format contracts.

Single source of truth for endpoint paths, multipart field names, JSON
metadata field names, response field names, and the dimensionality
invariants that protect the no-cross-model-comparison rule.

Mirrors:

* ``cloud/main.py`` — the FastAPI server implementing these endpoints.
* ``edge/cloud_client.py`` — the edge client posting against them.

This module is intentionally cv2 / numpy / fastapi free so it can be
imported on any host without pulling in heavy dependencies. Anything
runtime-shaped belongs in ``edge/`` or ``cloud/``.

Compatibility:
    Adding fields to ``METADATA_FIELDS`` or ``VERIFICATION_RESPONSE_FIELDS``
    is forward-compatible (older edge clients ignore unknown server
    fields; older servers ignore unknown client fields).
    Renaming or removing a field is a wire-format break — bump the
    contract version in lockstep on both edge and cloud.
"""
from __future__ import annotations

from typing import Dict, Final, Tuple

# ── Contract identity ─────────────────────────────────────────────────────────
CONTRACT_VERSION: Final[str] = "1.0"

# ── HTTP endpoints (cloud-side) ───────────────────────────────────────────────
VERIFY_IMAGE_PATH: Final[str] = "/verify/image"
HEALTH_PATH: Final[str] = "/health"
ENROLL_PATH: Final[str] = "/enroll"
GALLERY_STATS_PATH: Final[str] = "/gallery/stats"

# ── Multipart fields on POST /verify/image ────────────────────────────────────
MULTIPART_IMAGE_FIELD: Final[str] = "image"
MULTIPART_METADATA_FIELD: Final[str] = "metadata"

# ── Edge → cloud metadata JSON (Form["metadata"]) ─────────────────────────────
METADATA_FIELDS: Tuple[str, ...] = (
    "session_id",
    "frame_id",
    "track_id",
    "edge_confidence",
    "edge_candidate",
    "routing_strategy",
    "timestamp_edge_ms",
)

# ── Cloud → edge VerificationResponse ─────────────────────────────────────────
VERIFICATION_RESPONSE_FIELDS: Tuple[str, ...] = (
    "verified",
    "identity",
    "arcface_confidence",
    "edge_candidate",
    "edge_cloud_agree",
    "image_decode_ms",
    "arcface_extract_ms",
    "gallery_search_ms",
    "server_total_ms",
    "route",
    "request_id",
    "timestamp_server_ms",
    "gallery_size",
)

# ── Embedding-space invariants ────────────────────────────────────────────────
# Critical: MobileFaceNet (edge) and ArcFace (cloud) embeddings live in
# different geometric spaces and are NEVER compared directly. Offload uses
# JPEG face crops only. These constants exist so any future code can
# defensively assert dimensionality at gallery / wire boundaries.
ARCFACE_EMBEDDING_DIM: Final[int] = 512
MOBILEFACENET_EMBEDDING_DIM_CLASSIC: Final[int] = 128
MOBILEFACENET_EMBEDDING_DIM_QUANTISED: Final[int] = 192
MOBILEFACENET_EMBEDDING_DIMS: Tuple[int, ...] = (
    MOBILEFACENET_EMBEDDING_DIM_CLASSIC,
    MOBILEFACENET_EMBEDDING_DIM_QUANTISED,
)

# ── Defaults that both sides must agree on ────────────────────────────────────
DEFAULT_JPEG_QUALITY: Final[int] = 85
DEFAULT_TIMEOUT_S: Final[float] = 2.0
DEFAULT_CLOUD_PORT: Final[int] = 8000

# ── Telemetry ingestion endpoints (cloud_backend) ─────────────────────────────
# These are served by ``cloud_backend.server:app`` (the composite backend) and
# are absent from the verification-only ``cloud.main:app`` entry point.
TELEMETRY_SESSION_START_PATH: Final[str] = "/telemetry/sessions/start"
TELEMETRY_SESSION_END_PATH: Final[str] = "/telemetry/sessions/end"
TELEMETRY_INGEST_PATH: Final[str] = "/telemetry/ingest"
TELEMETRY_HEALTH_PATH: Final[str] = "/telemetry/healthz"

# ── Dashboard read APIs ───────────────────────────────────────────────────────
DASHBOARD_API_PREFIX: Final[str] = "/api"
SESSIONS_LIST_PATH: Final[str] = "/api/sessions"
SESSION_DETAIL_PATH_TEMPLATE: Final[str] = "/api/sessions/{session_id}"
SESSION_TELEMETRY_PATH_TEMPLATE: Final[str] = "/api/sessions/{session_id}/telemetry"
SESSION_SUMMARY_PATH_TEMPLATE: Final[str] = "/api/sessions/{session_id}/summary"
EXPERIMENTS_LIST_PATH: Final[str] = "/api/experiments"
EXPERIMENT_DETAIL_PATH_TEMPLATE: Final[str] = "/api/experiments/{experiment_label}"
METRICS_AGREEMENT_PATH: Final[str] = "/api/metrics/agreement"
METRICS_OFFLOAD_PATH: Final[str] = "/api/metrics/offload"
METRICS_LATENCY_PATH: Final[str] = "/api/metrics/latency"

# ── Live telemetry WebSocket ──────────────────────────────────────────────────
WS_TELEMETRY_PATH: Final[str] = "/ws/telemetry"

# ── Telemetry batching defaults (edge uploader / ingest endpoint) ─────────────
DEFAULT_TELEMETRY_BATCH_SIZE: Final[int] = 64
DEFAULT_TELEMETRY_FLUSH_INTERVAL_S: Final[float] = 10.0
DEFAULT_TELEMETRY_MAX_QUEUE: Final[int] = 4096
DEFAULT_TELEMETRY_RETRY_BACKOFF_S: Final[float] = 5.0
DEFAULT_TELEMETRY_MAX_RETRIES: Final[int] = 3

# Event-type vocabulary (loose; new types are forward-compatible). Listed
# so dashboards know what to expect when querying /api/sessions/.../telemetry.
TELEMETRY_EVENT_TYPES: Tuple[str, ...] = (
    "session_start",
    "session_end",
    "frame_telemetry",      # one row per frame (mirrors telemetry_log.csv)
    "diagnostic",           # one row per (frame, track) decision (mirrors diagnostic_log.csv)
    "attendance",           # one row per matched event (mirrors attendance_log.csv)
    "offload",              # one row per cloud verification attempt
    "report",               # post-run experiment report summary
)

# ── Stabilization / experimentation endpoints (cloud_backend) ─────────────────
METRICS_STABILIZATION_PATH: Final[str] = "/api/metrics/stabilization"
METRICS_ORIENTATION_PATH: Final[str] = "/api/metrics/orientation"
METRICS_PAD_PATH: Final[str] = "/api/metrics/pad"
METRICS_THERMAL_PATH: Final[str] = "/api/metrics/thermal"
METRICS_THRESHOLD_SWEEP_PATH: Final[str] = "/api/metrics/threshold_sweep"
METRICS_CONFIDENCE_DISTRIBUTION_PATH: Final[str] = "/api/metrics/confidence_distribution"
SESSION_PROTOCOL_PATH_TEMPLATE: Final[str] = "/api/sessions/{session_id}/protocol"
SESSION_CATEGORY_PATH_TEMPLATE: Final[str] = "/api/sessions/{session_id}/category"

# ── Experiment protocol vocabulary ────────────────────────────────────────────
# Free-text tags carried in experiment_protocol.json. Documented vocabulary so
# dashboards / analysis tools can offer consistent UI; "other" / arbitrary
# values are still accepted at the wire level.
EXPERIMENT_PROTOCOL_VERSION: Final[str] = "1.0"

ATTACK_TYPES: Tuple[str, ...] = (
    "none",
    "print",
    "screen_replay",
    "video_replay",
    "mask_paper",
    "mask_silicone",
    "mask_resin",
    "deepfake",
    "occlusion",
    "other",
)

LIGHTING_LABELS: Tuple[str, ...] = (
    "bright",
    "normal",
    "dim",
    "backlit",
    "side_lit",
    "uneven",
    "outdoor_sunny",
    "outdoor_cloudy",
)

ORIENTATION_LABELS: Tuple[str, ...] = (
    "frontal",
    "tilted",
    "overhead",
    "side",
    "mixed",
)

MOUNTING_LABELS: Tuple[str, ...] = (
    "tripod_eye_level",
    "tripod_overhead",
    "wall_mount",
    "ceiling_mount",
    "desk_clip",
    "handheld",
    "other",
)

MOVEMENT_LABELS: Tuple[str, ...] = (
    "static",
    "slow_walk",
    "fast_walk",
    "approach",
    "retreat",
    "lateral",
    "rotation",
    "mixed",
)

# ── Stabilization metric keys (vocab for /api/metrics/stabilization) ──────────
STABILIZATION_METRIC_KEYS: Tuple[str, ...] = (
    "orientation_mode_flip_rate",
    "orientation_ratio_std",
    "confidence_rolling_std",
    "confidence_drift",
    "detection_persistence_mean",
    "bbox_centroid_std",
    "bbox_area_cv",
    "blur_mean",
    "blur_std",
    "pad_real_fraction",
    "pad_spoof_fraction",
    "pad_uncertain_fraction",
    "offload_trigger_rate",
    "thermal_p95_c",
    "thermal_throttle_warnings",
)

# ── Quality-gate tags (vocab for /api/metrics/quality_tags) ───────────────────
# Soft tags; presence in a session's tag list signals an observability issue,
# never a runtime rejection. The brief lists examples explicitly; the rest are
# documented derivations.
QUALITY_TAGS: Tuple[str, ...] = (
    "unstable_camera",        # bbox area CV exceeds threshold
    "excessive_blur",         # mean avg_blur below threshold
    "low_light",              # mean brightness below threshold
    "excessive_proximity",    # fraction of distance frames near MIN_DISTANCE
    "unstable_tracking",      # detection persistence mean below threshold
    "thermal_warning",        # cpu_temp_c over threshold fraction
    "low_confidence_run",     # mean sim across REAL frames below threshold
    "frequent_spoof_flips",   # PAD label flip rate exceeds threshold
    "excessive_offload",      # OFFLOAD_TO_CLOUD fraction exceeds threshold
    "identity_flicker",       # any track sees N+ distinct identities
    "orientation_unstable",   # mode flip rate mean exceeds threshold
    "high_offload_failure",   # cloud_outcome != "success" rate too high
)

# Soft severity labels emitted with each tag. "info" never blocks; "warn"
# surfaces in dashboards; "alert" is the loudest signal but still soft.
QUALITY_SEVERITIES: Tuple[str, ...] = ("info", "warn", "alert")

# Default thresholds for the gate evaluator. These are research-grade
# starting values; sessions tag aggressively rather than miss. Operators
# override via the CLI ``--threshold KEY=VALUE`` syntax.
QUALITY_GATE_DEFAULTS: Dict[str, float] = {
    "bbox_area_cv_warn": 0.30,
    "bbox_area_cv_alert": 0.60,
    "blur_p50_warn": 80.0,        # avg_blur is Laplacian variance; smaller = blurrier
    "blur_p50_alert": 40.0,
    "brightness_p50_warn": 60.0,  # 8-bit mean
    "brightness_p50_alert": 30.0,
    "proximity_close_frac_warn": 0.15,    # fraction of frames within 0.5m of MIN_DISTANCE
    "proximity_close_frac_alert": 0.30,
    "active_fraction_warn": 0.70,         # detection_persistence.mean_active_fraction
    "active_fraction_alert": 0.40,
    "thermal_over_rate_warn": 0.05,
    "thermal_over_rate_alert": 0.20,
    "sim_real_mean_warn": 0.65,
    "sim_real_mean_alert": 0.50,
    "pad_flip_rate_warn": 0.05,
    "pad_flip_rate_alert": 0.15,
    "offload_rate_warn": 0.20,
    "offload_rate_alert": 0.40,
    "identity_distinct_warn": 2,          # per-track distinct identity count
    "identity_distinct_alert": 4,
    "mode_flip_rate_warn": 0.10,
    "mode_flip_rate_alert": 0.25,
    "offload_failure_rate_warn": 0.20,
    "offload_failure_rate_alert": 0.50,
}

# ── Quality / stabilization endpoints (cloud_backend, pass 6) ─────────────────
METRICS_QUALITY_TAGS_PATH: Final[str] = "/api/metrics/quality_tags"
SESSION_QUALITY_TAGS_PATH_TEMPLATE: Final[str] = "/api/sessions/{session_id}/quality_tags"


def is_valid_arcface_dim(dim: int) -> bool:
    """Gallery / wire-format guard for the cloud side."""
    return dim == ARCFACE_EMBEDDING_DIM


def is_valid_mobilefacenet_dim(dim: int) -> bool:
    """Edge-side guard for the local enrollment / extraction path."""
    return dim in MOBILEFACENET_EMBEDDING_DIMS


__all__ = [
    "CONTRACT_VERSION",
    "VERIFY_IMAGE_PATH",
    "HEALTH_PATH",
    "ENROLL_PATH",
    "GALLERY_STATS_PATH",
    "MULTIPART_IMAGE_FIELD",
    "MULTIPART_METADATA_FIELD",
    "METADATA_FIELDS",
    "VERIFICATION_RESPONSE_FIELDS",
    "ARCFACE_EMBEDDING_DIM",
    "MOBILEFACENET_EMBEDDING_DIM_CLASSIC",
    "MOBILEFACENET_EMBEDDING_DIM_QUANTISED",
    "MOBILEFACENET_EMBEDDING_DIMS",
    "DEFAULT_JPEG_QUALITY",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_CLOUD_PORT",
    "TELEMETRY_SESSION_START_PATH",
    "TELEMETRY_SESSION_END_PATH",
    "TELEMETRY_INGEST_PATH",
    "TELEMETRY_HEALTH_PATH",
    "DASHBOARD_API_PREFIX",
    "SESSIONS_LIST_PATH",
    "SESSION_DETAIL_PATH_TEMPLATE",
    "SESSION_TELEMETRY_PATH_TEMPLATE",
    "SESSION_SUMMARY_PATH_TEMPLATE",
    "EXPERIMENTS_LIST_PATH",
    "EXPERIMENT_DETAIL_PATH_TEMPLATE",
    "METRICS_AGREEMENT_PATH",
    "METRICS_OFFLOAD_PATH",
    "METRICS_LATENCY_PATH",
    "WS_TELEMETRY_PATH",
    "DEFAULT_TELEMETRY_BATCH_SIZE",
    "DEFAULT_TELEMETRY_FLUSH_INTERVAL_S",
    "DEFAULT_TELEMETRY_MAX_QUEUE",
    "DEFAULT_TELEMETRY_RETRY_BACKOFF_S",
    "DEFAULT_TELEMETRY_MAX_RETRIES",
    "TELEMETRY_EVENT_TYPES",
    "METRICS_STABILIZATION_PATH",
    "METRICS_ORIENTATION_PATH",
    "METRICS_PAD_PATH",
    "METRICS_THERMAL_PATH",
    "METRICS_THRESHOLD_SWEEP_PATH",
    "METRICS_CONFIDENCE_DISTRIBUTION_PATH",
    "SESSION_PROTOCOL_PATH_TEMPLATE",
    "SESSION_CATEGORY_PATH_TEMPLATE",
    "EXPERIMENT_PROTOCOL_VERSION",
    "ATTACK_TYPES",
    "LIGHTING_LABELS",
    "ORIENTATION_LABELS",
    "MOUNTING_LABELS",
    "MOVEMENT_LABELS",
    "STABILIZATION_METRIC_KEYS",
    "QUALITY_TAGS",
    "QUALITY_SEVERITIES",
    "QUALITY_GATE_DEFAULTS",
    "METRICS_QUALITY_TAGS_PATH",
    "SESSION_QUALITY_TAGS_PATH_TEMPLATE",
    "is_valid_arcface_dim",
    "is_valid_mobilefacenet_dim",
]
