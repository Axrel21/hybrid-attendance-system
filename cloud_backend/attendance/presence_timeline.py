"""Presence timeline aggregation — anonymous sessions, in-memory only (D3 Track 5)."""

from __future__ import annotations

import logging
import threading
import time

from cloud_backend.system.settings import get_settings
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("cloud_backend.attendance.presence_timeline")

PresenceStatus = Literal["active", "inactive"]


@dataclass
class PresenceSession:
    camera_id: str
    track_id: int
    first_seen: int
    last_seen: int
    status: PresenceStatus
    handoff_identity: str | None = None
    handoff_confidence: str | None = None
    continuity_label: str | None = None
    continuity_note: str | None = None
    continuity_similarity: float | None = None
    continuity_score: float | None = None
    continuity_confidence: str | None = None
    continuity_recovered_from_track: int | None = None
    continuity_recovery_age_ms: int | None = None

    @property
    def duration_sec(self) -> int:
        return max(0, (self.last_seen - self.first_seen) // 1000)

    def to_dict(self) -> dict:
        payload = {
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "duration_sec": self.duration_sec,
            "status": self.status,
        }
        if self.handoff_identity:
            payload["handoff_identity"] = self.handoff_identity
            payload["handoff_confidence"] = self.handoff_confidence
        if self.continuity_label:
            payload["continuity_label"] = self.continuity_label
            payload["continuity_note"] = self.continuity_note
            if self.continuity_similarity is not None:
                payload["continuity_similarity"] = self.continuity_similarity
            if self.continuity_score is not None:
                payload["continuity_score"] = self.continuity_score
            if self.continuity_confidence:
                payload["continuity_confidence"] = self.continuity_confidence
            if self.continuity_recovered_from_track is not None:
                payload["continuity_recovered_from_track"] = self.continuity_recovered_from_track
            if self.continuity_recovery_age_ms is not None:
                payload["continuity_recovery_age_ms"] = self.continuity_recovery_age_ms
        return payload


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
        handoff_identity: str | None = None,
        handoff_confidence: str | None = None,
        continuity_label: str | None = None,
        continuity_note: str | None = None,
        continuity_similarity: float | None = None,
        continuity_score: float | None = None,
        continuity_confidence: str | None = None,
        continuity_recovered_from_track: int | None = None,
        continuity_recovery_age_ms: int | None = None,
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
                    handoff_identity=handoff_identity,
                    handoff_confidence=handoff_confidence,
                    continuity_label=continuity_label,
                    continuity_note=continuity_note,
                    continuity_similarity=continuity_similarity,
                    continuity_score=continuity_score,
                    continuity_confidence=continuity_confidence,
                    continuity_recovered_from_track=continuity_recovered_from_track,
                    continuity_recovery_age_ms=continuity_recovery_age_ms,
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
        stale_keys = [
            key
            for key, session in self._sessions.items()
            if now_ms - session.last_seen > self._timeout_ms
        ]
        if stale_keys:
            from cloud_backend.system.observability import log_ops

            for key in stale_keys:
                session = self._sessions[key]
                log_ops(
                    log,
                    "SURVEILLANCE",
                    f"Session timeout: track={session.track_id} camera={session.camera_id} "
                    f"duration={session.duration_sec}s",
                )
        for key in stale_keys:
            del self._sessions[key]

    def list_sessions(
        self,
        camera_id: str | None = None,
        *,
        include_inactive: bool = False,
    ) -> list[PresenceSession]:
        with self._lock:
            self._expire_stale(int(time.time() * 1000))

            sessions = list(self._sessions.values())
            if not include_inactive:
                sessions = [s for s in sessions if s.status == "active"]
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
