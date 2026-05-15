# shared/__init__.py
"""Cross-cutting contracts shared by the edge runtime and the cloud backend.

This package is **not** a runtime node. It contains only:

* HTTP / wire-format constants (:mod:`shared.contracts`)
* Lazy accessors for per-run CSV schemas (:mod:`shared.schemas`)
* Cross-process invariants (embedding dimensionality, default ports, ...)

Anything pulling in cv2, InsightFace, TFLite, FastAPI, or pandas belongs
in :mod:`edge` or :mod:`cloud` — not here. Keeping ``shared`` dependency-light
means dashboard / packaging / aggregation tooling can pull from it without
also installing the Pi or cloud runtime stack.

Operational placement:
    * The Raspberry Pi bundle (``deployment/pi/PI_BUNDLE.txt``) ships
      ``shared/`` alongside ``edge/``.
    * The ArcFace server bundle (``deployment/cloud/CLOUD_BUNDLE.txt``)
      ships ``shared/`` alongside ``cloud/``.
    * Future telemetry aggregation / dashboard services on the cloud host
      should consume contracts from here rather than re-defining strings.
"""
from .contracts import (
    ARCFACE_EMBEDDING_DIM,
    CONTRACT_VERSION,
    DEFAULT_CLOUD_PORT,
    DEFAULT_JPEG_QUALITY,
    DEFAULT_TIMEOUT_S,
    ENROLL_PATH,
    GALLERY_STATS_PATH,
    HEALTH_PATH,
    METADATA_FIELDS,
    MOBILEFACENET_EMBEDDING_DIM_CLASSIC,
    MOBILEFACENET_EMBEDDING_DIM_QUANTISED,
    MOBILEFACENET_EMBEDDING_DIMS,
    MULTIPART_IMAGE_FIELD,
    MULTIPART_METADATA_FIELD,
    VERIFICATION_RESPONSE_FIELDS,
    VERIFY_IMAGE_PATH,
    is_valid_arcface_dim,
    is_valid_mobilefacenet_dim,
)
from .schemas import (
    ATTENDANCE_CSV_COLUMNS,
    EXPERIMENT_INDEX_FIELDS,
    get_diag_columns,
    get_telemetry_csv_columns,
)

__all__ = [
    # contracts
    "ARCFACE_EMBEDDING_DIM",
    "CONTRACT_VERSION",
    "DEFAULT_CLOUD_PORT",
    "DEFAULT_JPEG_QUALITY",
    "DEFAULT_TIMEOUT_S",
    "ENROLL_PATH",
    "GALLERY_STATS_PATH",
    "HEALTH_PATH",
    "METADATA_FIELDS",
    "MOBILEFACENET_EMBEDDING_DIM_CLASSIC",
    "MOBILEFACENET_EMBEDDING_DIM_QUANTISED",
    "MOBILEFACENET_EMBEDDING_DIMS",
    "MULTIPART_IMAGE_FIELD",
    "MULTIPART_METADATA_FIELD",
    "VERIFICATION_RESPONSE_FIELDS",
    "VERIFY_IMAGE_PATH",
    "is_valid_arcface_dim",
    "is_valid_mobilefacenet_dim",
    # schemas
    "ATTENDANCE_CSV_COLUMNS",
    "EXPERIMENT_INDEX_FIELDS",
    "get_diag_columns",
    "get_telemetry_csv_columns",
]
