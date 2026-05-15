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

from typing import Final, Tuple

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
    "is_valid_arcface_dim",
    "is_valid_mobilefacenet_dim",
]
