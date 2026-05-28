"""Map track continuity to presence events (appeared / disappeared / heartbeat)."""

from __future__ import annotations

import os
import time

import numpy as np

from surveillance.appearance_extract import appearance_continuity_enabled, extract_track_embedding
from surveillance.presence_client import SurveillancePresenceClient

RECOVERY_WINDOW_MS = 12_000
RECOVERY_COOLDOWN_MS = 2000
MAX_RECOVERY_EXTRACTIONS_PER_WINDOW = 3
RECOVERY_RATE_WINDOW_MS = 15_000


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


RECOVERY_WINDOW_MS = _env_int("SURVEILLANCE_RECOVERY_WINDOW_MS", RECOVERY_WINDOW_MS)
RECOVERY_COOLDOWN_MS = _env_int("SURVEILLANCE_RECOVERY_COOLDOWN_MS", RECOVERY_COOLDOWN_MS)


def _norm_track_meta(
    bbox: tuple[int, int, int, int] | None,
    frame: np.ndarray | None,
) -> tuple[float | None, float | None, list[float] | None]:
    if bbox is None or frame is None or frame.size == 0:
        return None, None, None
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return None, None, None
    x1, y1, x2, y2 = bbox
    cx = ((x1 + x2) / 2.0) / width
    cy = ((y1 + y2) / 2.0) / height
    norm_bbox = [x1 / width, y1 / height, x2 / width, y2 / height]
    return cx, cy, norm_bbox


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
        self._prev_frame: np.ndarray | None = None
        self._prev_bboxes: dict[int, tuple[int, int, int, int]] = {}
        self._last_disappear_ms: dict[int, int] = {}
        self._track_first_seen_ms: dict[int, int] = {}
        self._last_recovery_extract_ms = 0
        self._recovery_extract_times: list[int] = []

    def observe(
        self,
        active_track_ids: list[int],
        occupancy: int,
        *,
        track_entry_zone: dict[int, bool] | None = None,
        frame: np.ndarray | None = None,
        track_bboxes: dict[int, tuple[int, int, int, int]] | None = None,
    ) -> None:
        """Emit appeared/disappeared/heartbeat based on current tracks. Never raises."""
        current = set(active_track_ids)
        entry_zone = track_entry_zone or {}
        bboxes = track_bboxes or {}
        now_ms = int(time.time() * 1000)
        appearance_on = appearance_continuity_enabled()

        for track_id in sorted(self._prev_ids - current):
            embedding = None
            bbox = self._prev_bboxes.get(track_id)
            meta_frame = self._prev_frame
            cx, cy, norm_bbox = _norm_track_meta(bbox, meta_frame)
            first_seen = self._track_first_seen_ms.get(track_id, now_ms)
            duration_sec = max(0, (now_ms - first_seen) // 1000)
            if appearance_on and meta_frame is not None and bbox is not None:
                embedding = extract_track_embedding(meta_frame, bbox)
            self._last_disappear_ms[track_id] = now_ms
            self._track_first_seen_ms.pop(track_id, None)
            self._client.emit(
                track_id=track_id,
                event="disappeared",
                occupancy=occupancy,
                appearance_embedding=embedding,
                appearance_trigger="lost" if embedding else None,
                track_centroid_x=cx,
                track_centroid_y=cy,
                track_bbox=norm_bbox,
                track_duration_sec=duration_sec,
            )

        recent_disappear = any(
            now_ms - ts <= RECOVERY_WINDOW_MS for ts in self._last_disappear_ms.values()
        )
        self._prune_disappear_times(now_ms)

        for track_id in sorted(current - self._prev_ids):
            in_zone = bool(entry_zone.get(track_id))
            bbox = bboxes.get(track_id)
            cx, cy, norm_bbox = _norm_track_meta(bbox, frame)
            self._track_first_seen_ms[track_id] = now_ms

            should_extract = appearance_on and frame is not None
            trigger = None
            if should_extract:
                if in_zone:
                    trigger = "entry"
                elif recent_disappear and self._can_extract_recovery(now_ms):
                    trigger = "recovery"

            embedding = None
            if should_extract and trigger and bbox is not None:
                embedding = extract_track_embedding(frame, bbox)
                if trigger == "recovery" and embedding is not None:
                    self._record_recovery_extract(now_ms)

            self._client.emit(
                track_id=track_id,
                event="appeared",
                occupancy=occupancy,
                in_entry_zone=in_zone,
                appearance_embedding=embedding,
                appearance_trigger=trigger if embedding else None,
                track_centroid_x=cx,
                track_centroid_y=cy,
                track_bbox=norm_bbox,
            )

        self._prev_ids = current
        self._prev_bboxes = dict(bboxes)
        if frame is not None:
            self._prev_frame = frame

        now = time.monotonic()
        if now - self._last_heartbeat >= self._heartbeat_interval_s:
            self._last_heartbeat = now
            self._client.emit(track_id=0, event="heartbeat", occupancy=occupancy)

    def _can_extract_recovery(self, now_ms: int) -> bool:
        if now_ms - self._last_recovery_extract_ms < RECOVERY_COOLDOWN_MS:
            return False
        cutoff = now_ms - RECOVERY_RATE_WINDOW_MS
        self._recovery_extract_times = [ts for ts in self._recovery_extract_times if ts >= cutoff]
        return len(self._recovery_extract_times) < MAX_RECOVERY_EXTRACTIONS_PER_WINDOW

    def _record_recovery_extract(self, now_ms: int) -> None:
        self._last_recovery_extract_ms = now_ms
        self._recovery_extract_times.append(now_ms)

    def _prune_disappear_times(self, now_ms: int) -> None:
        cutoff = now_ms - RECOVERY_WINDOW_MS
        self._last_disappear_ms = {
            track_id: ts for track_id, ts in self._last_disappear_ms.items() if ts >= cutoff
        }

    def flush(self) -> None:
        self._client.flush()


def build_presence_sync(client: SurveillancePresenceClient) -> PresenceSync:
    raw = os.environ.get("SURVEILLANCE_HEARTBEAT_S", "30")
    try:
        interval = float(raw)
    except ValueError:
        interval = 30.0
    return PresenceSync(client, heartbeat_interval_s=max(5.0, interval))
