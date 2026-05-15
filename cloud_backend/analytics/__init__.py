# cloud_backend/analytics/__init__.py
"""Pure-function analytics helpers.

Three submodules:

* :mod:`cloud_backend.analytics.metrics` — research-grade aggregations
  (ROC / FAR / FRR / EER, agreement, offload-outcome, latency
  percentiles).
* :mod:`cloud_backend.analytics.stabilization` — stabilization
  diagnostics (orientation stability, confidence stability, PAD
  temporal, thermal, bbox stability) over the cloud event stream.
* :mod:`cloud_backend.analytics.calibration` — threshold sweep,
  hysteresis count, confidence distribution.

Kept dependency-light (``numpy`` required; ``pandas`` not imported) so
the dashboard router can call them synchronously without a worker pool.
"""
from . import calibration, metrics, quality, stabilization

__all__ = ["calibration", "metrics", "quality", "stabilization"]
