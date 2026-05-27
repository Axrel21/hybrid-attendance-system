"""In-memory derived attendance state cache (D5 Track 2)."""

from __future__ import annotations

import threading

from cloud_backend.attendance.schemas.derived_state import AttendanceStateRecord


class AttendanceStateStore:
    """Thread-safe recomputable state layer — not the ORM attendance_records table."""

    def __init__(self) -> None:
        self._records: list[AttendanceStateRecord] = []
        self._lock = threading.Lock()

    def replace(self, records: list[AttendanceStateRecord]) -> None:
        with self._lock:
            self._records = list(records)

    def list_records(self, lecture_id: str | None = None) -> list[AttendanceStateRecord]:
        with self._lock:
            if lecture_id is None:
                return list(self._records)
            return [record for record in self._records if record.lecture_id == lecture_id]


_store = AttendanceStateStore()


def get_state_store() -> AttendanceStateStore:
    return _store
