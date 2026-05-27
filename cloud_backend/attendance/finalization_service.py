"""AttendanceFinalizationService — freeze states when lecture ends (D5 Track 3)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.eligibility_queries import fetch_lecture
from cloud_backend.attendance.finalization_store import get_finalization_store
from cloud_backend.attendance.schemas.derived_state import AttendanceStateRecord
from cloud_backend.attendance.schemas.finalized import AttendanceFinalizedRecord
from cloud_backend.attendance.state_service import get_state_service
from cloud_backend.sessions.lifecycle import LectureStatus

log = logging.getLogger("cloud_backend.attendance.finalization")

_FINALIZED_LECTURE_STATUSES = {
    LectureStatus.FINALIZED.value,
}


class AttendanceFinalizationService:
    """Apply lecture closure rules; freeze states for ended lectures."""

    async def build_records(
        self,
        session: AsyncSession,
        *,
        lecture_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[AttendanceFinalizedRecord]:
        live_states = await get_state_service().build_records(
            session,
            lecture_id=lecture_id,
            limit=limit,
        )

        lecture_ids = _lecture_ids_from_states(live_states, lecture_id)
        finalized_flags = await _lecture_finalized_flags(session, lecture_ids)

        results: list[AttendanceFinalizedRecord] = []
        by_lecture = _group_states_by_lecture(live_states)

        for lec_key, states in by_lecture.items():
            is_finalized = finalized_flags.get(lec_key, False) if lec_key else False

            if is_finalized and lec_key:
                frozen = get_finalization_store().get(lec_key)
                if frozen is None:
                    frozen = [
                        _to_finalized_record(_apply_closure(state), finalized=True)
                        for state in states
                    ]
                    get_finalization_store().set(lec_key, frozen)
                    from cloud_backend.system.observability import log_event

                    log_event(
                        log,
                        "finalization_frozen",
                        lecture_id=lec_key,
                        count=len(frozen),
                    )
                results.extend(frozen)
            else:
                results.extend(
                    [_to_finalized_record(state, finalized=False) for state in states]
                )

        if lecture_id is not None:
            lecture_key = str(lecture_id)
            results = [record for record in results if record.lecture_id == lecture_key]

        results.sort(key=lambda record: (record.lecture_id or "", record.student_id))
        from cloud_backend.system.observability import log_event

        log_event(
            log,
            "finalization_generated",
            total=len(results),
            lecture_id=str(lecture_id) if lecture_id else None,
        )
        return results


def _lecture_ids_from_states(
    states: list[AttendanceStateRecord],
    filter_lecture_id: uuid.UUID | None,
) -> set[str]:
    if filter_lecture_id is not None:
        return {str(filter_lecture_id)}
    return {state.lecture_id for state in states if state.lecture_id}


async def _lecture_finalized_flags(
    session: AsyncSession,
    lecture_ids: set[str],
) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for lecture_key in lecture_ids:
        try:
            lecture_uuid = uuid.UUID(lecture_key)
        except ValueError:
            flags[lecture_key] = False
            continue
        lecture = await fetch_lecture(session, lecture_uuid)
        if lecture is None:
            flags[lecture_key] = False
            continue
        flags[lecture_key] = lecture.status in _FINALIZED_LECTURE_STATUSES
    return flags


def _group_states_by_lecture(
    states: list[AttendanceStateRecord],
) -> dict[str | None, list[AttendanceStateRecord]]:
    grouped: dict[str | None, list[AttendanceStateRecord]] = {}
    for state in states:
        grouped.setdefault(state.lecture_id, []).append(state)
    return grouped


def _apply_closure(state: AttendanceStateRecord) -> AttendanceStateRecord:
    if state.attendance_state == "candidate":
        return state.model_copy(
            update={
                "attendance_state": "expired",
                "reason": "lecture_finalized_candidate_expired",
            }
        )
    return state


def _to_finalized_record(
    state: AttendanceStateRecord,
    *,
    finalized: bool,
) -> AttendanceFinalizedRecord:
    return AttendanceFinalizedRecord(
        student_id=state.student_id,
        attendance_state=state.attendance_state,
        finalized=finalized,
        lecture_id=state.lecture_id,
        source=state.source,
    )


def get_finalization_service() -> AttendanceFinalizationService:
    return AttendanceFinalizationService()
