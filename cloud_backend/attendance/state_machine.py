"""Attendance state enum — import-safe, no side effects on import."""

from __future__ import annotations

import enum


class AttendanceState(str, enum.Enum):
    """Per-student attendance state within an active lecture session."""

    UNDETECTED = "undetected"
    CANDIDATE = "candidate"
    INITIALIZED = "initialized"
    CONFIRMED = "confirmed"
    ABSENT = "absent"
    LATE_ENTRY = "late_entry"
    TECH_DROPOUT = "tech_dropout"
    MANUAL_OVERRIDE = "manual_override"
