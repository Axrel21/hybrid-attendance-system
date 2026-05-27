# cloud_backend/experiments/registry.py
"""Read-side projection: sessions grouped by experiment_label.

Backed entirely by :class:`cloud_backend.storage.TelemetryStorage`. No
duplication of state; if the on-disk store is wiped, the registry returns
empty results next call.

Adds in pass 5: :func:`categorize_session`, which derives a canonical
short key + dimension class from the (optional) experiment-protocol
sub-dict in session metadata.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from cloud_backend.storage import SessionRecord, TelemetryStorage

log = logging.getLogger("cloud_backend.experiments")


# ── Auto-categorization ──────────────────────────────────────────────────────

def _distance_bucket(distance_m: Optional[float]) -> str:
    if distance_m is None:
        return "unknown"
    try:
        d = float(distance_m)
    except (TypeError, ValueError):
        return "unknown"
    if d <= 0:
        return "unknown"
    if d < 1.0:
        return "close"
    if d < 2.5:
        return "mid"
    return "far"


def categorize_session(metadata: Dict[str, Any]) -> Dict[str, str]:
    """Derive a short canonical category key from session metadata.

    Reads the optional ``protocol`` sub-dict (populated by the edge
    uploader from ``experiments/exp_<id>/config/experiment_protocol.json``).
    Returns a stable dict so dashboards can group sessions without
    re-parsing free-text fields.
    """
    protocol = (metadata or {}).get("protocol") or {}
    attack_type = (protocol.get("attack_type") or "unknown").strip().lower()
    attack_class = "genuine" if attack_type in ("", "none") else attack_type
    orientation = (protocol.get("orientation") or "unknown").strip().lower()
    lighting = (protocol.get("lighting") or "unknown").strip().lower()
    distance = protocol.get("distance_m")
    dbucket = _distance_bucket(distance)
    category = f"{orientation}_{attack_class}_{lighting}_{dbucket}"
    return {
        "session_id": metadata.get("session_id") or "",
        "category": category,
        "attack_class": attack_class,
        "orientation_class": orientation,
        "lighting_class": lighting,
        "distance_bucket": dbucket,
    }


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

    def session_protocol(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return the ``protocol`` sub-dict of session metadata, if any."""
        detail = self.storage.get_session(session_id)
        if detail is None:
            return None
        return (detail.get("metadata") or {}).get("protocol")

    def session_category(self, session_id: str) -> Optional[Dict[str, str]]:
        detail = self.storage.get_session(session_id)
        if detail is None:
            return None
        return categorize_session(detail.get("metadata") or {})

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
