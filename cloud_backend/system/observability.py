"""Structured and operational logging helpers (D5 Track 5)."""

from __future__ import annotations

import logging
from typing import Any, Callable

# Polling-triggered structured events — kept at DEBUG only.
_SUPPRESSED_EVENTS = frozenset(
    {
        "evidence_generated",
        "report_generated",
        "eligibility_generated",
        "decision_generated",
        "finalization_generated",
    }
)


def log_ops(
    logger: logging.Logger,
    tag: str,
    message: str,
    *,
    level: int = logging.INFO,
) -> None:
    """Emit a compact operational line: [TAG] message."""
    if not logger.isEnabledFor(level):
        return
    record = logger.makeRecord(
        logger.name,
        level,
        "(observability)",
        0,
        message,
        (),
        None,
    )
    record.ops_tag = tag
    logger.handle(record)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit structured or operational logs depending on event type."""
    if event in _SUPPRESSED_EVENTS:
        parts = [f"event={event}"]
        for key, value in sorted(fields.items()):
            if value is not None:
                parts.append(f"{key}={value}")
        logger.debug(" ".join(parts))
        return

    formatter = _EVENT_FORMATTERS.get(event)
    if formatter is not None:
        rendered = formatter(**fields)
        if rendered is None:
            return
        tag = _EVENT_TAGS.get(event, "INFO")
        log_ops(logger, tag, rendered)
        return

    parts = [f"event={event}"]
    for key, value in sorted(fields.items()):
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


def _fmt_recognition(
    *,
    accepted: bool | None = None,
    disposition: str | None = None,
    gallery_identity: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    confidence: float | None = None,
    source: str | None = None,
    **_extra: Any,
) -> str | None:
    identity = gallery_identity or "unknown"
    conf = f" conf={confidence:.2f}" if confidence is not None else ""
    src = (source or "").lower()

    if disposition == "transitioned" and from_state and to_state:
        return f"Attendance transition: {identity} {from_state}→{to_state}{conf}"

    if accepted and disposition in ("accepted", "transitioned"):
        if src == "edge_runtime" or "cloud" in src:
            label = "Cloud offload accepted" if "cloud" in src else "Recognition accepted"
        else:
            label = "Local recognition accepted"
        return f"{label}: {identity}{conf}"

    if accepted is False or disposition in (
        "no_active_lecture",
        "no_active_lecture_in_classroom",
        "unknown_identity",
        "not_enrolled",
        "unknown_camera",
        "unknown_classroom",
        "window_closed",
        "engine_skip",
        "suppressed",
    ):
        reason = disposition or "rejected"
        return f"Recognition rejected: {identity} reason={reason}{conf}"

    return f"Recognition event: {identity} disposition={disposition}{conf}"


def _fmt_presence(
    *,
    camera_id: str | None = None,
    track_id: int | None = None,
    presence_event: str | None = None,
    occupancy: int | None = None,
    **_extra: Any,
) -> str | None:
    event = (presence_event or "").lower()
    cam = camera_id or "—"
    tid = track_id if track_id is not None else "—"

    if event == "heartbeat":
        return None

    if event == "appeared":
        return f"Track active: id={tid} camera={cam}"

    if event == "disappeared":
        return f"Track ended: id={tid} camera={cam}"

    if occupancy is not None:
        return f"Occupancy change: camera={cam} count={occupancy}"

    return f"Presence event: camera={cam} track={tid} event={event or 'unknown'}"


def _fmt_finalization_frozen(*, lecture_id: str | None = None, count: int | None = None, **_extra: Any) -> str:
    return f"Lecture frozen: id={lecture_id} records={count or 0}"


_EVENT_TAGS: dict[str, str] = {
    "recognition_ingested": "RECOGNITION",
    "presence_ingested": "SURVEILLANCE",
    "finalization_frozen": "ATTENDANCE",
}

_EVENT_FORMATTERS: dict[str, Callable[..., str | None]] = {
    "recognition_ingested": _fmt_recognition,
    "presence_ingested": _fmt_presence,
    "finalization_frozen": _fmt_finalization_frozen,
}
