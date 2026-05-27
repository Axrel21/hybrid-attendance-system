"""In-memory presence event log — surveillance only, no attendance (D3 Track 4)."""

from __future__ import annotations

import threading
from typing import Any

_MAX_ENTRIES = 500


class PresenceEventStore:
    """Thread-safe ring buffer for recent presence events."""

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries :]

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries[-limit:])

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_store = PresenceEventStore()


def get_presence_store() -> PresenceEventStore:
    return _store
