"""Experimental doorway handoff — in-memory temporal-spatial correlation only.

NOT identity tracking. Best-effort annotation on anonymous surveillance tracks.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

HANDOFF_TTL_MS = 4000
HANDOFF_TEMPORAL_THRESHOLD_MS = 5000


@dataclass(frozen=True)
class PendingRecognition:
    identity: str
    timestamp_ms: int
    classroom_id: uuid.UUID | None


@dataclass(frozen=True)
class HandoffAnnotation:
    handoff_identity: str
    handoff_confidence: str = "temporal_spatial"


class DoorwayHandoffQueue:
    """Short-lived recognition events awaiting optional entry-zone correlation."""

    def __init__(self) -> None:
        self._pending: list[PendingRecognition] = []
        self._lock = threading.Lock()

    def push(
        self,
        *,
        identity: str,
        timestamp_ms: int,
        classroom_id: uuid.UUID | None = None,
    ) -> None:
        now_ms = int(time.time() * 1000)
        ts = timestamp_ms if timestamp_ms is not None else now_ms
        with self._lock:
            self._prune_locked(now_ms)
            self._pending.append(
                PendingRecognition(
                    identity=identity,
                    timestamp_ms=ts,
                    classroom_id=classroom_id,
                )
            )

    def try_match(self, *, track_timestamp_ms: int) -> HandoffAnnotation | None:
        """Return annotation only when exactly one pending event matches temporally."""
        with self._lock:
            self._prune_locked(track_timestamp_ms)
            candidates = [
                item
                for item in self._pending
                if abs(track_timestamp_ms - item.timestamp_ms) <= HANDOFF_TEMPORAL_THRESHOLD_MS
            ]
            if len(candidates) != 1:
                return None
            return HandoffAnnotation(handoff_identity=candidates[0].identity)

    def _prune_locked(self, now_ms: int) -> None:
        cutoff = now_ms - HANDOFF_TTL_MS
        self._pending = [item for item in self._pending if item.timestamp_ms >= cutoff]


_queue: DoorwayHandoffQueue | None = None
_queue_lock = threading.Lock()


def get_doorway_handoff_queue() -> DoorwayHandoffQueue:
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = DoorwayHandoffQueue()
        return _queue
