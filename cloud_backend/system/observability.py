"""Structured logging helpers (D5 Track 5)."""

from __future__ import annotations

import logging
from typing import Any


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit a single-line structured log: event=key k=v ..."""
    parts = [f"event={event}"]
    for key, value in sorted(fields.items()):
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))
