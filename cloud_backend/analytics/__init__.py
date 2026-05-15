# cloud_backend/analytics/__init__.py
"""Pure-function analytics helpers — ROC / FAR / FRR / EER, agreement,
offload-outcome, latency percentiles.

Kept dependency-light (``numpy`` required; ``pandas`` optional) so the
dashboard router can call them synchronously without a worker pool.
"""
from . import metrics

__all__ = ["metrics"]
