"""Frozen attendance state snapshots per ended lecture (D5 Track 3)."""

from __future__ import annotations

import threading

from cloud_backend.attendance.schemas.finalized import AttendanceFinalizedRecord


class AttendanceFinalizationStore:
    """In-memory frozen states by lecture_id — not ORM attendance_records."""

    def __init__(self) -> None:
        self._frozen: dict[str, list[AttendanceFinalizedRecord]] = {}
        self._lock = threading.Lock()

    def get(self, lecture_id: str) -> list[AttendanceFinalizedRecord] | None:
        with self._lock:
            stored = self._frozen.get(lecture_id)
            if stored is None:
                return None
            return list(stored)

    def set(self, lecture_id: str, records: list[AttendanceFinalizedRecord]) -> None:
        with self._lock:
            self._frozen[lecture_id] = list(records)

    def clear(self) -> None:
        with self._lock:
            self._frozen.clear()


_store = AttendanceFinalizationStore()


def get_finalization_store() -> AttendanceFinalizationStore:
    return _store
