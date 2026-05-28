"""Experimental appearance-assisted continuity — in-memory, trigger-based only.

NOT production ReID. No PostgreSQL persistence. No attendance FSM coupling.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass

MEMORY_TTL_MS = 120_000
LOST_TTL_MS = 12_000
RECOVERY_WINDOW_MS = 12_000
MIN_RECOVERY_SCORE = 0.75
AMBIGUITY_MARGIN = 0.05
MAX_LOST_BUFFER = 24
MAX_RECOVERY_ANNOTATIONS_PER_WINDOW = 5
RECOVERY_ANNOTATION_WINDOW_MS = 15_000

WEIGHT_EMBEDDING = 0.65
WEIGHT_TEMPORAL = 0.20
WEIGHT_SPATIAL = 0.15
SPATIAL_MAX_DIST = 0.18


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


LOST_TTL_MS = _env_int("APPEARANCE_LOST_TTL_MS", LOST_TTL_MS)
RECOVERY_WINDOW_MS = min(LOST_TTL_MS, _env_int("APPEARANCE_RECOVERY_WINDOW_MS", RECOVERY_WINDOW_MS))


@dataclass(frozen=True)
class IdentityTrackMemory:
    identity: str
    camera_id: str
    track_id: int
    embedding: tuple[float, ...]
    timestamp_ms: int
    handoff_confidence: str | None = None


@dataclass(frozen=True)
class LostTrackMemory:
    camera_id: str
    track_id: int
    embedding: tuple[float, ...]
    timestamp_ms: int
    centroid_x: float | None = None
    centroid_y: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    track_duration_sec: int = 0
    identity: str | None = None
    handoff_confidence: str | None = None


@dataclass(frozen=True)
class ContinuityAnnotation:
    continuity_label: str
    continuity_note: str
    continuity_similarity: float | None = None
    continuity_score: float | None = None
    continuity_confidence: str | None = None
    recovered_from_track_id: int | None = None
    recovery_age_ms: int | None = None


def cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def temporal_score(delta_ms: int, window_ms: int = RECOVERY_WINDOW_MS) -> float:
    if delta_ms < 0 or delta_ms > window_ms:
        return 0.0
    return 1.0 - (delta_ms / window_ms)


def spatial_score(
    *,
    centroid_a: tuple[float, float] | None,
    centroid_b: tuple[float, float] | None,
    max_dist: float = SPATIAL_MAX_DIST,
) -> float:
    if centroid_a is None or centroid_b is None:
        return 0.5
    dx = centroid_a[0] - centroid_b[0]
    dy = centroid_a[1] - centroid_b[1]
    dist = math.sqrt(dx * dx + dy * dy)
    if dist >= max_dist:
        return 0.0
    return 1.0 - (dist / max_dist)


def bbox_scale_similarity(
    bbox_a: tuple[float, float, float, float] | None,
    bbox_b: tuple[float, float, float, float] | None,
) -> float:
    if bbox_a is None or bbox_b is None:
        return 0.5

    def area(box: tuple[float, float, float, float]) -> float:
        return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])

    area_a = area(bbox_a)
    area_b = area(bbox_b)
    if area_a <= 1e-9 or area_b <= 1e-9:
        return 0.5
    return min(area_a, area_b) / max(area_a, area_b)


def weighted_recovery_score(
    *,
    embedding_similarity: float,
    delta_ms: int,
    centroid_new: tuple[float, float] | None,
    centroid_lost: tuple[float, float] | None,
    bbox_new: tuple[float, float, float, float] | None,
    bbox_lost: tuple[float, float, float, float] | None,
) -> float:
    temp = temporal_score(delta_ms)
    spat = spatial_score(centroid_a=centroid_new, centroid_b=centroid_lost)
    scale = bbox_scale_similarity(bbox_new, bbox_lost)
    spatial_blend = (0.7 * spat) + (0.3 * scale)
    score = (
        (WEIGHT_EMBEDDING * embedding_similarity)
        + (WEIGHT_TEMPORAL * temp)
        + (WEIGHT_SPATIAL * spatial_blend)
    )
    return max(0.0, min(1.0, score))


def confidence_label_for_score(score: float) -> str | None:
    if score > 0.90:
        return "Strong continuity candidate"
    if score >= 0.80:
        return "Possible continuity"
    if score >= MIN_RECOVERY_SCORE:
        return "Weak similarity"
    return None


class AppearanceContinuityService:
    """TTL-backed in-memory continuity hints — best effort, ambiguous-safe."""

    def __init__(self) -> None:
        self._identity_tracks: list[IdentityTrackMemory] = []
        self._lost_tracks: list[LostTrackMemory] = []
        self._recovery_annotation_times: list[int] = []
        self._lock = threading.Lock()

    def register_entry_handoff(
        self,
        *,
        identity: str,
        camera_id: str,
        track_id: int,
        embedding: tuple[float, ...],
        timestamp_ms: int,
        handoff_confidence: str | None = None,
    ) -> ContinuityAnnotation:
        with self._lock:
            self._prune_locked(timestamp_ms)
            self._identity_tracks.append(
                IdentityTrackMemory(
                    identity=identity,
                    camera_id=camera_id,
                    track_id=track_id,
                    embedding=embedding,
                    timestamp_ms=timestamp_ms,
                    handoff_confidence=handoff_confidence,
                )
            )
        return ContinuityAnnotation(
            continuity_label="entry_appearance_association",
            continuity_note="Experimental appearance continuity at doorway handoff",
            continuity_confidence="Advisory continuity signal",
        )

    def register_lost_track(
        self,
        *,
        camera_id: str,
        track_id: int,
        embedding: tuple[float, ...],
        timestamp_ms: int,
        centroid_x: float | None = None,
        centroid_y: float | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        track_duration_sec: int = 0,
    ) -> None:
        identity, handoff_conf = self._identity_for_track(camera_id, track_id)
        with self._lock:
            self._prune_locked(timestamp_ms)
            self._lost_tracks.append(
                LostTrackMemory(
                    camera_id=camera_id,
                    track_id=track_id,
                    embedding=embedding,
                    timestamp_ms=timestamp_ms,
                    centroid_x=centroid_x,
                    centroid_y=centroid_y,
                    bbox=bbox,
                    track_duration_sec=max(0, track_duration_sec),
                    identity=identity,
                    handoff_confidence=handoff_conf,
                )
            )
            if len(self._lost_tracks) > MAX_LOST_BUFFER:
                self._lost_tracks = self._lost_tracks[-MAX_LOST_BUFFER:]

    def try_recovery_match(
        self,
        *,
        camera_id: str,
        track_id: int,
        embedding: tuple[float, ...],
        timestamp_ms: int,
        centroid_x: float | None = None,
        centroid_y: float | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> ContinuityAnnotation | None:
        with self._lock:
            self._prune_locked(timestamp_ms)
            if not self._can_annotate_recovery_locked(timestamp_ms):
                return None

            centroid_new = (
                (centroid_x, centroid_y)
                if centroid_x is not None and centroid_y is not None
                else None
            )
            scored: list[tuple[LostTrackMemory, float, float]] = []

            for lost in self._lost_tracks:
                if lost.camera_id != camera_id:
                    continue
                if lost.track_id == track_id:
                    continue
                delta_ms = timestamp_ms - lost.timestamp_ms
                if delta_ms < 0 or delta_ms > RECOVERY_WINDOW_MS:
                    continue

                emb_sim = cosine_similarity(embedding, lost.embedding)
                centroid_lost = (
                    (lost.centroid_x, lost.centroid_y)
                    if lost.centroid_x is not None and lost.centroid_y is not None
                    else None
                )
                score = weighted_recovery_score(
                    embedding_similarity=emb_sim,
                    delta_ms=delta_ms,
                    centroid_new=centroid_new,
                    centroid_lost=centroid_lost,
                    bbox_new=bbox,
                    bbox_lost=lost.bbox,
                )
                if score >= MIN_RECOVERY_SCORE:
                    scored.append((lost, score, emb_sim))

            if not scored:
                return None

            scored.sort(key=lambda item: item[1], reverse=True)
            best_lost, best_score, best_emb = scored[0]
            if len(scored) > 1 and scored[1][1] >= best_score - AMBIGUITY_MARGIN:
                return None

            confidence = confidence_label_for_score(best_score)
            if confidence is None:
                return None

            self._recovery_annotation_times.append(timestamp_ms)
            age_ms = timestamp_ms - best_lost.timestamp_ms
            note = (
                f"Advisory recovered continuity from track #{best_lost.track_id} "
                f"({age_ms // 1000}s ago, sim {int(best_emb * 100)}%)"
            )
            return ContinuityAnnotation(
                continuity_label="possible_recovered_continuity",
                continuity_note=note,
                continuity_similarity=round(best_emb, 3),
                continuity_score=round(best_score, 3),
                continuity_confidence=confidence,
                recovered_from_track_id=best_lost.track_id,
                recovery_age_ms=age_ms,
            )

    def _identity_for_track(
        self, camera_id: str, track_id: int
    ) -> tuple[str | None, str | None]:
        for item in reversed(self._identity_tracks):
            if item.camera_id == camera_id and item.track_id == track_id:
                return item.identity, item.handoff_confidence
        return None, None

    def _can_annotate_recovery_locked(self, now_ms: int) -> bool:
        cutoff = now_ms - RECOVERY_ANNOTATION_WINDOW_MS
        self._recovery_annotation_times = [
            ts for ts in self._recovery_annotation_times if ts >= cutoff
        ]
        return len(self._recovery_annotation_times) < MAX_RECOVERY_ANNOTATIONS_PER_WINDOW

    def _prune_locked(self, now_ms: int) -> None:
        identity_cutoff = now_ms - MEMORY_TTL_MS
        lost_cutoff = now_ms - LOST_TTL_MS
        self._identity_tracks = [
            item for item in self._identity_tracks if item.timestamp_ms >= identity_cutoff
        ]
        self._lost_tracks = [
            item for item in self._lost_tracks if item.timestamp_ms >= lost_cutoff
        ]


_service: AppearanceContinuityService | None = None
_service_lock = threading.Lock()


def get_appearance_continuity_service() -> AppearanceContinuityService:
    global _service
    with _service_lock:
        if _service is None:
            _service = AppearanceContinuityService()
        return _service
