"""Presence timeline aggregation — anonymous sessions, in-memory only (D3 Track 5)."""

from __future__ import annotations

import threading
import time

from cloud_backend.system.settings import get_settings
from dataclasses import dataclass
from typing import Literal

PresenceStatus = Literal["active", "inactive"]


@dataclass
class PresenceSession:
    camera_id: str
    track_id: int
    first_seen: int
    last_seen: int
    status: PresenceStatus

    @property
    def duration_sec(self) -> int:
        return max(0, (self.last_seen - self.first_seen) // 1000)

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "duration_sec": self.duration_sec,
            "status": self.status,
        }


def _default_timeout_ms() -> int:
    seconds = get_settings().attendance.presence_session_timeout_s
    return max(5, int(seconds * 1000))


class PresenceTimelineService:
    """Consume presence events and maintain anonymous track sessions."""

    def __init__(self, timeout_ms: int | None = None) -> None:
        self._timeout_ms = timeout_ms if timeout_ms is not None else _default_timeout_ms()
        self._sessions: dict[tuple[str, int], PresenceSession] = {}
        self._lock = threading.Lock()

    def on_event(
        self,
        *,
        camera_id: str,
        track_id: int,
        event: str,
        timestamp_ms: int,
    ) -> None:
        """Apply appeared / heartbeat / disappeared to session state."""
        with self._lock:
            self._expire_stale(timestamp_ms)

            if event == "appeared" and track_id > 0:
                self._sessions[(camera_id, track_id)] = PresenceSession(
                    camera_id=camera_id,
                    track_id=track_id,
                    first_seen=timestamp_ms,
                    last_seen=timestamp_ms,
                    status="active",
                )
                return

            if event == "disappeared" and track_id > 0:
                session = self._sessions.get((camera_id, track_id))
                if session is None:
                    return
                session.last_seen = timestamp_ms
                session.status = "inactive"
                return

            if event == "heartbeat":
                for key, session in self._sessions.items():
                    if key[0] != camera_id or session.status != "active" or key[1] <= 0:
                        continue
                    session.last_seen = timestamp_ms

    def _expire_stale(self, now_ms: int) -> None:
        for session in self._sessions.values():
            if session.status != "active":
                continue
            if now_ms - session.last_seen > self._timeout_ms:
                session.status = "inactive"

    def list_sessions(self, camera_id: str | None = None) -> list[PresenceSession]:
        with self._lock:
            self._expire_stale(int(time.time() * 1000))

            sessions = list(self._sessions.values())
            if camera_id is not None:
                sessions = [s for s in sessions if s.camera_id == camera_id]
            sessions.sort(key=lambda s: (s.camera_id, s.track_id))
            return sessions


_timeline: PresenceTimelineService | None = None
_timeline_lock = threading.Lock()


def get_timeline_service() -> PresenceTimelineService:
    global _timeline
    with _timeline_lock:
        if _timeline is None:
            _timeline = PresenceTimelineService()
        return _timeline
