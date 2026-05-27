"""Lecture lifecycle status enum — import-safe, no side effects on import."""

from __future__ import annotations

import enum


class LectureStatus(str, enum.Enum):
    """Lifecycle status for a scheduled lecture session."""

    SCHEDULED = "scheduled"
    ACTIVE_WINDOW_OPEN = "active_window_open"
    ACTIVE_WINDOW_CLOSED = "active_window_closed"
    FINALIZED = "finalized"
