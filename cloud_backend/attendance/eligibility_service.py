"""AttendanceEligibilityService — presence ratio advisory (D4 Track 2)."""

from __future__ import annotations

import logging
import uuid

from cloud_backend.system.settings import get_settings
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.eligibility_queries import fetch_lecture, lecture_duration_sec
from cloud_backend.attendance.evidence_service import get_evidence_service
from cloud_backend.attendance.schemas.eligibility import AttendanceEligibilityRecord
from cloud_backend.attendance.schemas.evidence import AttendanceEvidenceRecord

log = logging.getLogger("cloud_backend.attendance.eligibility")


def _eligibility_threshold() -> float:
    return min(1.0, max(0.0, get_settings().attendance.eligibility_threshold))


class AttendanceEligibilityService:
    """Compute eligibility from evidence and presence durations. Read-only."""

    async def build_records(
        self,
        session: AsyncSession,
        *,
        lecture_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[AttendanceEligibilityRecord]:
        evidence_records = await get_evidence_service().build_records(
            session,
            lecture_id=lecture_id,
            limit=limit,
        )

        grouped: dict[tuple[str, str | None], list[AttendanceEvidenceRecord]] = defaultdict(list)
        for record in evidence_records:
            grouped[(record.student_id, record.lecture_id)].append(record)

        results: list[AttendanceEligibilityRecord] = []
        for (student_id, lecture_key), records in grouped.items():
            results.append(
                await self._decide_for_student(
                    session,
                    student_id=student_id,
                    lecture_id=lecture_key,
                    evidence_records=records,
                )
            )

        results.sort(key=lambda r: (r.lecture_id or "", r.student_id))
        from cloud_backend.system.observability import log_event

        log_event(
            log,
            "eligibility_generated",
            total=len(results),
            lecture_id=str(lecture_id) if lecture_id else None,
        )
        return results

    async def _decide_for_student(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        lecture_id: str | None,
        evidence_records: list[AttendanceEvidenceRecord],
    ) -> AttendanceEligibilityRecord:
        lecture_duration = 0
        if lecture_id:
            lecture = await fetch_lecture(session, uuid.UUID(lecture_id))
            if lecture is not None:
                lecture_duration = lecture_duration_sec(lecture)

        presence_records = [r for r in evidence_records if r.evidence == "presence_observed"]
        if not presence_records:
            return AttendanceEligibilityRecord(
                student_id=student_id,
                presence_duration_sec=0,
                lecture_duration_sec=lecture_duration,
                presence_ratio=0.0,
                decision="unknown",
                lecture_id=lecture_id,
            )

        presence_duration = 0
        for record in presence_records:
            presence_duration = max(presence_duration, record.presence_duration_sec)

        if lecture_duration <= 0:
            return AttendanceEligibilityRecord(
                student_id=student_id,
                presence_duration_sec=presence_duration,
                lecture_duration_sec=0,
                presence_ratio=0.0,
                decision="unknown",
                lecture_id=lecture_id,
            )

        ratio = presence_duration / lecture_duration
        threshold = _eligibility_threshold()
        if ratio >= threshold:
            decision = "eligible"
        else:
            decision = "insufficient_presence"

        return AttendanceEligibilityRecord(
            student_id=student_id,
            presence_duration_sec=presence_duration,
            lecture_duration_sec=lecture_duration,
            presence_ratio=round(ratio, 4),
            decision=decision,
            lecture_id=lecture_id,
        )


def get_eligibility_service() -> AttendanceEligibilityService:
    return AttendanceEligibilityService()
