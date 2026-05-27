"""AttendanceStateService — decision → derived attendance state (D5 Track 2)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.decision_service import get_decision_service
from cloud_backend.attendance.schemas.decision import AttendanceDecisionRecord
from cloud_backend.attendance.schemas.derived_state import AttendanceStateRecord
from cloud_backend.attendance.state_store import get_state_store

log = logging.getLogger("cloud_backend.attendance.state")

_STATE_SOURCE = "decision_engine"


class AttendanceStateService:
    """Derive attendance states from advisory decisions. Read-only vs ORM attendance."""

    async def build_records(
        self,
        session: AsyncSession,
        *,
        lecture_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[AttendanceStateRecord]:
        decision_records = await get_decision_service().build_records(
            session,
            lecture_id=lecture_id,
            limit=limit,
        )

        states = [self._from_decision(decision) for decision in decision_records]
        states.sort(key=lambda record: (record.lecture_id or "", record.student_id))

        get_state_store().replace(states)
        log.debug(
            "attendance states built total=%d lecture_id=%s",
            len(states),
            lecture_id,
        )
        return states

    def _from_decision(self, decision: AttendanceDecisionRecord) -> AttendanceStateRecord:
        attendance_state, state_reason = _map_decision_to_state(decision.decision)
        return AttendanceStateRecord(
            student_id=decision.student_id,
            attendance_state=attendance_state,
            source=_STATE_SOURCE,
            lecture_id=decision.lecture_id,
            decision=decision.decision,
            reason=state_reason if state_reason else decision.reason,
        )


def _map_decision_to_state(decision: str | None) -> tuple[str, str]:
    if decision == "present":
        return "confirmed", "decision_present"
    if decision == "absent":
        return "insufficient_presence", "decision_absent"
    if decision == "manual_review":
        return "manual_review", "decision_manual_review"
    return "candidate", "missing_decision"


def get_state_service() -> AttendanceStateService:
    return AttendanceStateService()
