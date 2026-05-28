"""Presence event handler — log only, never touches AttendanceEngine (D3 Track 4)."""

from __future__ import annotations

import logging

from cloud_backend.attendance.appearance_continuity import get_appearance_continuity_service
from cloud_backend.attendance.doorway_handoff import get_doorway_handoff_queue
from cloud_backend.attendance.presence_store import get_presence_store
from cloud_backend.attendance.presence_timeline import get_timeline_service
from cloud_backend.attendance.schemas.presence import PresenceEvent, PresenceEventResult

log = logging.getLogger("cloud_backend.attendance.presence")


def _embedding_tuple(payload: PresenceEvent) -> tuple[float, ...] | None:
    if not payload.appearance_embedding:
        return None
    return tuple(float(v) for v in payload.appearance_embedding)


def _bbox_tuple(payload: PresenceEvent) -> tuple[float, float, float, float] | None:
    if not payload.track_bbox or len(payload.track_bbox) != 4:
        return None
    return tuple(float(v) for v in payload.track_bbox)


def _apply_continuity(session_kwargs: dict, annotation) -> None:
    session_kwargs["continuity_label"] = annotation.continuity_label
    session_kwargs["continuity_note"] = annotation.continuity_note
    session_kwargs["continuity_similarity"] = annotation.continuity_similarity
    session_kwargs["continuity_score"] = annotation.continuity_score
    session_kwargs["continuity_confidence"] = annotation.continuity_confidence
    session_kwargs["continuity_recovered_from_track"] = annotation.recovered_from_track_id
    session_kwargs["continuity_recovery_age_ms"] = annotation.recovery_age_ms


class PresenceEventHandler:
    """Accept anonymous surveillance presence events."""

    def ingest(self, payload: PresenceEvent) -> PresenceEventResult:
        entry = {
            "camera_id": payload.camera_id,
            "track_id": payload.track_id,
            "event": payload.event,
            "timestamp_ms": payload.timestamp_ms,
            "occupancy": payload.occupancy,
        }
        if payload.in_entry_zone is not None:
            entry["in_entry_zone"] = payload.in_entry_zone
        if payload.appearance_trigger:
            entry["appearance_trigger"] = payload.appearance_trigger
        if payload.appearance_embedding:
            entry["appearance_embedding_len"] = len(payload.appearance_embedding)

        session_kwargs = {
            "handoff_identity": None,
            "handoff_confidence": None,
            "continuity_label": None,
            "continuity_note": None,
            "continuity_similarity": None,
            "continuity_score": None,
            "continuity_confidence": None,
            "continuity_recovered_from_track": None,
            "continuity_recovery_age_ms": None,
        }

        embedding = _embedding_tuple(payload)
        bbox = _bbox_tuple(payload)
        continuity = get_appearance_continuity_service()

        if payload.event == "appeared" and payload.track_id > 0 and payload.in_entry_zone:
            annotation = get_doorway_handoff_queue().try_match(
                track_timestamp_ms=payload.timestamp_ms,
            )
            if annotation is not None:
                session_kwargs["handoff_identity"] = annotation.handoff_identity
                session_kwargs["handoff_confidence"] = annotation.handoff_confidence
                if embedding is not None:
                    cont = continuity.register_entry_handoff(
                        identity=annotation.handoff_identity,
                        camera_id=payload.camera_id,
                        track_id=payload.track_id,
                        embedding=embedding,
                        timestamp_ms=payload.timestamp_ms,
                        handoff_confidence=annotation.handoff_confidence,
                    )
                    _apply_continuity(session_kwargs, cont)

        if payload.event == "disappeared" and payload.track_id > 0 and embedding is not None:
            continuity.register_lost_track(
                camera_id=payload.camera_id,
                track_id=payload.track_id,
                embedding=embedding,
                timestamp_ms=payload.timestamp_ms,
                centroid_x=payload.track_centroid_x,
                centroid_y=payload.track_centroid_y,
                bbox=bbox,
                track_duration_sec=payload.track_duration_sec or 0,
            )

        if (
            payload.event == "appeared"
            and payload.track_id > 0
            and embedding is not None
            and payload.appearance_trigger == "recovery"
            and not payload.in_entry_zone
        ):
            recovery = continuity.try_recovery_match(
                camera_id=payload.camera_id,
                track_id=payload.track_id,
                embedding=embedding,
                timestamp_ms=payload.timestamp_ms,
                centroid_x=payload.track_centroid_x,
                centroid_y=payload.track_centroid_y,
                bbox=bbox,
            )
            if recovery is not None:
                _apply_continuity(session_kwargs, recovery)

        get_presence_store().append(entry)
        get_timeline_service().on_event(
            camera_id=payload.camera_id,
            track_id=payload.track_id,
            event=payload.event,
            timestamp_ms=payload.timestamp_ms,
            **session_kwargs,
        )
        from cloud_backend.system.observability import log_event

        log_event(
            log,
            "presence_ingested",
            camera_id=payload.camera_id,
            track_id=payload.track_id,
            presence_event=payload.event,
            occupancy=payload.occupancy,
        )
        return PresenceEventResult(
            accepted=True,
            message="presence event received",
            camera_id=payload.camera_id,
            track_id=payload.track_id,
            event=payload.event,
            occupancy=payload.occupancy,
        )
