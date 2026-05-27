"""In-memory attendance evidence cache (D4 Track 1)."""

from __future__ import annotations

import threading

from cloud_backend.attendance.schemas.evidence import AttendanceEvidenceRecord


class AttendanceEvidenceStore:
    def __init__(self) -> None:
        self._records: list[AttendanceEvidenceRecord] = []
        self._lock = threading.Lock()

    def replace(self, records: list[AttendanceEvidenceRecord]) -> None:
        with self._lock:
            self._records = list(records)

    def list_records(self, lecture_id: str | None = None) -> list[AttendanceEvidenceRecord]:
        with self._lock:
            if lecture_id is None:
                return list(self._records)
            return [r for r in self._records if r.lecture_id == lecture_id]

    def record_count(self) -> int:
        with self._lock:
            return len(self._records)


_store = AttendanceEvidenceStore()


def get_evidence_store() -> AttendanceEvidenceStore:
    return _store
