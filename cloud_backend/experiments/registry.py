# cloud_backend/experiments/registry.py
"""Read-side projection: sessions grouped by experiment_label.

Backed entirely by :class:`cloud_backend.storage.TelemetryStorage`. No
duplication of state; if the on-disk store is wiped, the registry returns
empty results next call.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from cloud_backend.storage import SessionRecord, TelemetryStorage

log = logging.getLogger("cloud_backend.experiments")


class ExperimentRegistry:
    """Aggregate sessions by experiment_label."""

    def __init__(self, storage: TelemetryStorage) -> None:
        self.storage = storage

    def list_experiments(self) -> List[Dict[str, Any]]:
        groups: Dict[str, List[SessionRecord]] = defaultdict(list)
        for rec in self.storage.list_sessions():
            groups[rec.experiment_label].append(rec)
        out: List[Dict[str, Any]] = []
        for label, recs in sorted(groups.items(), key=lambda kv: kv[0]):
            firsts = [r.started_at for r in recs if r.started_at]
            ends = [r.ended_at for r in recs if r.ended_at]
            out.append({
                "experiment_label": label,
                "session_count": len(recs),
                "first_seen": min(firsts) if firsts else None,
                "last_seen": max(ends) if ends else None,
            })
        return out

    def experiment_summary(self, experiment_label: str) -> Optional[Dict[str, Any]]:
        recs = [
            r for r in self.storage.list_sessions()
            if r.experiment_label == experiment_label
        ]
        if not recs:
            return None
        firsts = [r.started_at for r in recs if r.started_at]
        ends = [r.ended_at for r in recs if r.ended_at]
        total_events = sum(r.event_count for r in recs)
        with_summary = sum(1 for r in recs if r.has_summary)
        attack_counter: Counter = Counter()
        # Lightweight attack-type breakdown by scanning session metadata
        # for ``notes`` / ``experiment_label`` substrings. Real attack
        # labelling lives in ``fields`` per-event; aggregating across
        # whole sessions is left for downstream analytics.
        for rec in recs:
            detail = self.storage.get_session(rec.session_id) or {}
            md = detail.get("metadata") or {}
            attack = md.get("attack_type") or md.get("notes") or ""
            if attack:
                attack_counter[str(attack)[:80]] += 1
        return {
            "experiment_label": experiment_label,
            "session_count": len(recs),
            "first_seen": min(firsts) if firsts else None,
            "last_seen": max(ends) if ends else None,
            "total_events": total_events,
            "sessions_with_summary": with_summary,
            "attack_type_breakdown": dict(attack_counter),
            "session_ids": [r.session_id for r in recs],
        }
