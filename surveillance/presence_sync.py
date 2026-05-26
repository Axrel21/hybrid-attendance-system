"""Map track continuity to presence events (appeared / disappeared / heartbeat)."""

from __future__ import annotations

import os
import time

from surveillance.presence_client import SurveillancePresenceClient


class PresenceSync:
    """Diff active track IDs and emit presence events via the client."""

    def __init__(
        self,
        client: SurveillancePresenceClient,
        *,
        heartbeat_interval_s: float = 30.0,
    ) -> None:
        self._client = client
        self._heartbeat_interval_s = heartbeat_interval_s
        self._prev_ids: set[int] = set()
        self._last_heartbeat = 0.0

    def observe(self, active_track_ids: list[int], occupancy: int) -> None:
        """Emit appeared/disappeared/heartbeat based on current tracks. Never raises."""
        current = set(active_track_ids)

        for track_id in sorted(current - self._prev_ids):
            self._client.emit(track_id=track_id, event="appeared", occupancy=occupancy)

        for track_id in sorted(self._prev_ids - current):
            self._client.emit(track_id=track_id, event="disappeared", occupancy=occupancy)

        self._prev_ids = current

        now = time.monotonic()
        if now - self._last_heartbeat >= self._heartbeat_interval_s:
            self._last_heartbeat = now
            self._client.emit(track_id=0, event="heartbeat", occupancy=occupancy)

    def flush(self) -> None:
        self._client.flush()


def build_presence_sync(client: SurveillancePresenceClient) -> PresenceSync:
    raw = os.environ.get("SURVEILLANCE_HEARTBEAT_S", "30")
    try:
        interval = float(raw)
    except ValueError:
        interval = 30.0
    return PresenceSync(client, heartbeat_interval_s=max(5.0, interval))
