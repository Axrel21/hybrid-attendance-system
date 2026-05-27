"""AttendanceEngine — sole authority for attendance state transitions.

Design invariants:
- No HTTP calls. No background tasks. No direct knowledge of the ingestor.
- All writes are synchronous within a caller-supplied AsyncSession.
- Source-agnostic: edge runtime and future surveillance emit identical inputs.
- Duplicate / backward transitions are suppressed via guard table + cooldown.
- is_locked records are never touched; caller must check before calling.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.state_machine import AttendanceState
from cloud_backend.models.attendance_event import AttendanceEvent
from cloud_backend.models.attendance_record import AttendanceRecord
from cloud_backend.sessions.lifecycle import LectureStatus

# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------

#: Minimum cosine-similarity confidence for an event to be accepted.
MIN_CONFIDENCE: float = 0.60

#: After a successful forward transition, ignore events for this duration.
#: Prevents rapid oscillation from redundant edge detections.
COOLDOWN_SECONDS: int = 5

#: How many accepted events needed to advance undetected → candidate.
CANDIDATE_HIT_COUNT: int = 1

#: How many accepted events needed to advance candidate → initialized.
INITIALIZED_HIT_COUNT: int = 3

#: How many accepted events needed to advance initialized → confirmed.
CONFIRMED_HIT_COUNT: int = 5

# ---------------------------------------------------------------------------
# Internal result dataclass
# ---------------------------------------------------------------------------

@dataclass
class TransitionResult:
    accepted: bool
    from_state: str
    to_state: str
    reason: str
    record_id: uuid.UUID
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Forward-only transition table
# ---------------------------------------------------------------------------

_FORWARD: dict[AttendanceState, AttendanceState] = {
    AttendanceState.UNDETECTED: AttendanceState.CANDIDATE,
    AttendanceState.CANDIDATE:  AttendanceState.INITIALIZED,
    AttendanceState.INITIALIZED: AttendanceState.CONFIRMED,
}

_HIT_THRESHOLD: dict[AttendanceState, int] = {
    AttendanceState.UNDETECTED:  CANDIDATE_HIT_COUNT,
    AttendanceState.CANDIDATE:   INITIALIZED_HIT_COUNT,
    AttendanceState.INITIALIZED: CONFIRMED_HIT_COUNT,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class AttendanceEngine:
    """Process recognition events and advance attendance state for one record.

    One engine instance per request; reuse the session supplied by the caller.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def process_recognition_event(
        self,
        *,
        record_id: uuid.UUID,
        lecture_status: str,
        confidence: float,
        source: str,
        timestamp_ms: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TransitionResult:
        """Evaluate a recognition event against the stored AttendanceRecord.

        Returns a TransitionResult describing whether a transition occurred.
        Does NOT commit — caller owns the transaction.
        """
        meta = meta or {}

        # --- guard: lecture window must be open --------------------------
        if lecture_status != LectureStatus.ACTIVE_WINDOW_OPEN.value:
            return TransitionResult(
                accepted=False,
                from_state="?",
                to_state="?",
                reason=f"lecture window is {lecture_status!r}, not active_window_open",
                record_id=record_id,
            )

        # --- guard: confidence threshold ---------------------------------
        if confidence < MIN_CONFIDENCE:
            return TransitionResult(
                accepted=False,
                from_state="?",
                to_state="?",
                reason=f"confidence {confidence:.3f} below threshold {MIN_CONFIDENCE}",
                record_id=record_id,
            )

        record = await self._session.get(AttendanceRecord, record_id)
        if record is None:
            return TransitionResult(
                accepted=False,
                from_state="?",
                to_state="?",
                reason="attendance record not found",
                record_id=record_id,
            )

        from_state_str = record.state

        # --- guard: locked records are untouchable -----------------------
        if record.is_locked:
            return TransitionResult(
                accepted=False,
                from_state=from_state_str,
                to_state=from_state_str,
                reason="record is locked (manual_override)",
                record_id=record_id,
            )

        # --- guard: already confirmed — no further progression -----------
        try:
            current = AttendanceState(record.state)
        except ValueError:
            return TransitionResult(
                accepted=False,
                from_state=from_state_str,
                to_state=from_state_str,
                reason=f"record has unknown state {record.state!r}",
                record_id=record_id,
            )

        if current == AttendanceState.CONFIRMED:
            return TransitionResult(
                accepted=False,
                from_state=from_state_str,
                to_state=from_state_str,
                reason="already confirmed — no further transition needed",
                record_id=record_id,
            )

        # --- guard: no forward path from this state (e.g. ABSENT) -------
        if current not in _FORWARD:
            return TransitionResult(
                accepted=False,
                from_state=from_state_str,
                to_state=from_state_str,
                reason=f"no forward transition from state {current.value!r}",
                record_id=record_id,
            )

        # --- cooldown: suppress rapid repeat events ---------------------
        if record.last_event_at is not None:
            last_dt = record.last_event_at
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = (_utcnow() - last_dt).total_seconds()
            if elapsed < COOLDOWN_SECONDS:
                return TransitionResult(
                    accepted=False,
                    from_state=from_state_str,
                    to_state=from_state_str,
                    reason=f"cooldown active: {elapsed:.1f}s < {COOLDOWN_SECONDS}s",
                    record_id=record_id,
                )

        # --- count accepted events already logged for this record --------
        # Use len(record.events) loaded via selectin; fast for normal class sizes.
        hit_count = len(record.events) + 1  # +1 = this event

        target_state = _FORWARD[current]
        needed = _HIT_THRESHOLD[current]

        if hit_count < needed:
            # Persist the accepted event so future requests can accumulate toward
            # the threshold.  Without this row, len(record.events) never grows
            # during the accumulation phase and the count stalls permanently.
            await self._log_accumulation_event(
                record=record,
                from_state=from_state_str,
                source=source,
                confidence=confidence,
                timestamp_ms=timestamp_ms,
                meta=meta,
            )
            return TransitionResult(
                accepted=True,
                from_state=from_state_str,
                to_state=from_state_str,
                reason=f"event accepted ({hit_count}/{needed}); awaiting threshold",
                record_id=record_id,
                meta={"hit_count": hit_count, "needed": needed},
            )

        # --- transition --------------------------------------------------
        now = _utcnow()
        event = AttendanceEvent(
            attendance_record_id=record.id,
            event_type="recognition_match",
            from_state=from_state_str,
            to_state=target_state.value,
            source=source,
            confidence=confidence,
            timestamp_ms=timestamp_ms,
            meta_json=json.dumps(meta) if meta else None,
        )
        self._session.add(event)

        await self._session.execute(
            update(AttendanceRecord)
            .where(AttendanceRecord.id == record.id)
            .values(
                state=target_state.value,
                last_event_at=now,
                confirmed_at=now if target_state == AttendanceState.CONFIRMED else None,
            )
        )

        return TransitionResult(
            accepted=True,
            from_state=from_state_str,
            to_state=target_state.value,
            reason="state advanced",
            record_id=record_id,
            meta={"hit_count": hit_count},
        )

    async def _log_accumulation_event(
        self,
        *,
        record: AttendanceRecord,
        from_state: str,
        source: str,
        confidence: float,
        timestamp_ms: int | None,
        meta: dict[str, Any],
    ) -> None:
        """Persist an accepted-but-below-threshold event and refresh cooldown."""
        now = _utcnow()
        event = AttendanceEvent(
            attendance_record_id=record.id,
            event_type="recognition_match",
            from_state=from_state,
            to_state=from_state,
            source=source,
            confidence=confidence,
            timestamp_ms=timestamp_ms,
            meta_json=json.dumps(meta) if meta else None,
        )
        self._session.add(event)
        await self._session.execute(
            update(AttendanceRecord)
            .where(AttendanceRecord.id == record.id)
            .values(last_event_at=now)
        )
        await self._session.flush()
