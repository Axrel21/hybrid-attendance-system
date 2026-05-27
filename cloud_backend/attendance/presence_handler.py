"""Presence event handler — log only, never touches AttendanceEngine (D3 Track 4)."""

from __future__ import annotations

import logging

from cloud_backend.attendance.doorway_handoff import get_doorway_handoff_queue
from cloud_backend.attendance.presence_store import get_presence_store
from cloud_backend.attendance.presence_timeline import get_timeline_service
from cloud_backend.attendance.schemas.presence import PresenceEvent, PresenceEventResult

log = logging.getLogger("cloud_backend.attendance.presence")


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

        handoff_identity = None
        handoff_confidence = None
        if payload.event == "appeared" and payload.track_id > 0 and payload.in_entry_zone:
            annotation = get_doorway_handoff_queue().try_match(
                track_timestamp_ms=payload.timestamp_ms,
            )
            if annotation is not None:
                handoff_identity = annotation.handoff_identity
                handoff_confidence = annotation.handoff_confidence

        get_presence_store().append(entry)
        get_timeline_service().on_event(
            camera_id=payload.camera_id,
            track_id=payload.track_id,
            event=payload.event,
            timestamp_ms=payload.timestamp_ms,
            handoff_identity=handoff_identity,
            handoff_confidence=handoff_confidence,
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
