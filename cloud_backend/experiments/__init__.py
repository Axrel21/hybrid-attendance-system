# cloud_backend/experiments/__init__.py
"""Experiment registry — projection of stored sessions grouped by label.

Currently a thin layer over :mod:`cloud_backend.storage`. Future
extensions (cross-run aggregation, attack-type breakdowns, ROC over a
whole label) live here.
"""
from .registry import ExperimentRegistry

__all__ = ["ExperimentRegistry"]
