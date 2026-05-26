"""AttendanceDecisionService — eligibility → decision advisory (D5 Track 1)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from cloud_backend.attendance.eligibility_service import get_eligibility_service
from cloud_backend.attendance.evidence_service import get_evidence_service
from cloud_backend.attendance.schemas.decision import AttendanceDecisionRecord
from cloud_backend.attendance.schemas.eligibility import AttendanceEligibilityRecord
from cloud_backend.attendance.schemas.evidence import AttendanceEvidenceRecord

log = logging.getLogger("cloud_backend.attendance.decision")

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


class AttendanceDecisionService:
    """Map eligibility + evidence confidence to attendance decisions. Read-only."""

    async def build_records(
        self,
        session: AsyncSession,
        *,
        lecture_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> list[AttendanceDecisionRecord]:
        eligibility_records = await get_eligibility_service().build_records(
            session,
            lecture_id=lecture_id,
            limit=limit,
        )
        evidence_records = await get_evidence_service().build_records(
            session,
            lecture_id=lecture_id,
            limit=limit,
        )
        confidence_by_student_lecture = _confidence_by_student_lecture(evidence_records)

        decisions: list[AttendanceDecisionRecord] = []
        for eligibility in eligibility_records:
            confidence = confidence_by_student_lecture.get(
                (eligibility.student_id, eligibility.lecture_id),
                "low",
            )
            decisions.append(self._decide(eligibility, confidence))

        decisions.sort(key=lambda record: (record.lecture_id or "", record.student_id))
        log.info(
            "attendance decisions built total=%d lecture_id=%s",
            len(decisions),
            lecture_id,
        )
        return decisions

    def _decide(
        self,
        eligibility: AttendanceEligibilityRecord,
        confidence: str,
    ) -> AttendanceDecisionRecord:
        eligibility_decision = eligibility.decision

        if eligibility_decision == "eligible":
            if confidence == "high":
                return AttendanceDecisionRecord(
                    student_id=eligibility.student_id,
                    decision="present",
                    reason="eligible_high_confidence",
                    lecture_id=eligibility.lecture_id,
                    presence_ratio=eligibility.presence_ratio,
                    confidence=confidence,
                )
            if confidence == "medium":
                return AttendanceDecisionRecord(
                    student_id=eligibility.student_id,
                    decision="present",
                    reason="eligible_medium_confidence",
                    lecture_id=eligibility.lecture_id,
                    presence_ratio=eligibility.presence_ratio,
                    confidence=confidence,
                )
            return AttendanceDecisionRecord(
                student_id=eligibility.student_id,
                decision="manual_review",
                reason="eligible_low_confidence",
                lecture_id=eligibility.lecture_id,
                presence_ratio=eligibility.presence_ratio,
                confidence=confidence,
            )

        if eligibility_decision == "insufficient_presence":
            return AttendanceDecisionRecord(
                student_id=eligibility.student_id,
                decision="absent",
                reason="insufficient_presence",
                lecture_id=eligibility.lecture_id,
                presence_ratio=eligibility.presence_ratio,
                confidence=confidence,
            )

        return AttendanceDecisionRecord(
            student_id=eligibility.student_id,
            decision="manual_review",
            reason="unknown_eligibility",
            lecture_id=eligibility.lecture_id,
            presence_ratio=eligibility.presence_ratio,
            confidence=confidence,
        )


def _confidence_by_student_lecture(
    evidence_records: list[AttendanceEvidenceRecord],
) -> dict[tuple[str, str | None], str]:
    best: dict[tuple[str, str | None], str] = {}
    for record in evidence_records:
        if record.evidence != "presence_observed":
            continue
        key = (record.student_id, record.lecture_id)
        current = best.get(key, "low")
        if _CONFIDENCE_RANK.get(record.confidence, 0) > _CONFIDENCE_RANK.get(current, 0):
            best[key] = record.confidence
    return best


def get_decision_service() -> AttendanceDecisionService:
    return AttendanceDecisionService()
