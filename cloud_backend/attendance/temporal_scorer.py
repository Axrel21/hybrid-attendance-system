"""Temporal evidence scoring — recognition vs presence appearance (D4 Track 4)."""

from __future__ import annotations

from typing import Literal

from cloud_backend.attendance.presence_timeline import PresenceSession
from cloud_backend.system.settings import get_settings

EvidenceConfidence = Literal["low", "medium", "high"]


def _temporal_window_sec() -> int:
    return max(1, get_settings().attendance.evidence_temporal_window_sec)


class TemporalEvidenceScorer:
    """Score confidence from recognition time vs presence appearance."""

    def __init__(self, window_sec: int | None = None) -> None:
        self.window_sec = window_sec if window_sec is not None else _temporal_window_sec()

    def time_delta_sec(self, recognized_at_ms: int, presence_first_seen_ms: int) -> int:
        """Seconds between recognition and presence appearance (first_seen)."""
        return abs(int(recognized_at_ms) - int(presence_first_seen_ms)) // 1000

    def confidence_from_delta(self, time_delta_sec: int) -> EvidenceConfidence:
        if time_delta_sec <= 30:
            return "high"
        if time_delta_sec <= 120:
            return "medium"
        return "low"

    def score(
        self,
        *,
        recognized_at_ms: int,
        presence_first_seen_ms: int,
    ) -> tuple[int, EvidenceConfidence]:
        delta = self.time_delta_sec(recognized_at_ms, presence_first_seen_ms)
        return delta, self.confidence_from_delta(delta)

    def within_window(self, recognized_at_ms: int, session: PresenceSession) -> bool:
        """True when recognition falls within ±window of the presence span."""
        window_ms = self.window_sec * 1000
        start = session.first_seen - window_ms
        end = session.last_seen + window_ms
        return start <= recognized_at_ms <= end

    def pick_session(
        self,
        sessions: list[PresenceSession],
        recognized_at_ms: int,
    ) -> PresenceSession:
        """Prefer in-window sessions; tie-break by smallest time delta, then duration."""
        if not sessions:
            raise ValueError("sessions must not be empty")

        windowed = [session for session in sessions if self.within_window(recognized_at_ms, session)]
        pool = windowed if windowed else list(sessions)

        overlapping = [
            session
            for session in pool
            if session.first_seen <= recognized_at_ms <= session.last_seen
        ]
        if overlapping:
            pool = overlapping

        active = [session for session in pool if session.status == "active"]
        if active:
            pool = active

        return min(
            pool,
            key=lambda session: (
                self.time_delta_sec(recognized_at_ms, session.first_seen),
                -session.duration_sec,
            ),
        )


def get_temporal_scorer() -> TemporalEvidenceScorer:
    return TemporalEvidenceScorer()
