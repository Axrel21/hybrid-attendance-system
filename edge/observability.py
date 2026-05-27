"""Unified experiment observability (D5 Track 1) — CSV + summary + plots only."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("attendance.runtime")

ATTENDANCE_STATES = frozenset({
    "undetected",
    "candidate",
    "confirmed",
    "manual_review",
    "expired",
    "insufficient_presence",
    "initialized",
})

TRANSITION_KEYS = frozenset({
    "undetected→candidate",
    "candidate→initialized",
    "initialized→confirmed",
    "initialized→expired",
    "none",
})


def map_offload_reason(
    routing_reason: str | None,
    *,
    cloud_outcome: str | None = None,
    frame_latency_ms: float = 0.0,
    latency_budget_ms: float = 0.0,
) -> str:
    """Map router / cloud signals to telemetry offload_reason."""
    reason = (routing_reason or "").lower()
    outcome = (cloud_outcome or "").lower()

    if outcome and any(x in outcome for x in ("timeout", "timed_out")):
        return "high_latency"
    if latency_budget_ms > 0 and frame_latency_ms > latency_budget_ms * 1.5:
        return "high_latency"

    if not reason and not outcome:
        return "none"
    if any(x in reason for x in ("force_", "hysteresis", "skipped_router", "skipped_circuit")):
        return "policy"
    if "confidence" in reason and "<" in reason:
        return "low_confidence"
    if outcome and outcome not in ("none", "skipped_router", "skipped_no_crop", "skipped_circuit_breaker"):
        return "low_confidence"
    if reason:
        return "policy"
    return "none"


def map_attendance_transition(from_state: str | None, to_state: str | None) -> str:
    if not from_state or not to_state:
        return "none"
    fs = str(from_state).strip().lower()
    ts = str(to_state).strip().lower()
    if fs == ts:
        return "none"
    key = f"{fs}→{ts}"
    if key in TRANSITION_KEYS:
        return key
    if ts == "expired" and fs == "initialized":
        return "initialized→expired"
    return "none"


def normalize_attendance_state(raw: str | None) -> str:
    if not raw:
        return "undetected"
    s = str(raw).strip().lower()
    if s in ATTENDANCE_STATES:
        if s == "initialized":
            return "candidate"
        return s
    if "insufficient" in s:
        return "insufficient_presence"
    if s in ("manual_override", "manual_review"):
        return "manual_review"
    if s == "expired":
        return "expired"
    return "undetected"


@dataclass
class FrameObservability:
    recognition_source: str = "none"
    offload_reason: str = "none"
    attendance_transition: str = "none"
    surveillance_present: str = "false"
    lecture_active: str = "false"
    attendance_state: str = "undetected"


@dataclass
class ExperimentObservability:
    """Per-run observability; frame rollup + sticky attendance / presence context."""

    cloud_health_interval_s: float = 30.0
    _frame: FrameObservability = field(default_factory=FrameObservability)
    attendance_state: str = "undetected"
    attendance_transition: str = "none"
    lecture_active: bool = False
    surveillance_present: bool = False
    _last_health_poll: float = 0.0
    _cloud_base_url: str = ""

    def configure_cloud_poll(self, base_url: str) -> None:
        self._cloud_base_url = (base_url or "").rstrip("/")

    def reset_frame(self) -> None:
        self._frame = FrameObservability(
            surveillance_present="true" if self.surveillance_present else "false",
            lecture_active="true" if self.lecture_active else "false",
            attendance_state=self.attendance_state,
            attendance_transition="none",
        )

    def observe_track(
        self,
        dbg: dict[str, Any],
        *,
        routing_reason: str | None = None,
        routing_should_offload: bool | None = None,
        frame_latency_ms: float = 0.0,
        latency_budget_ms: float = 0.0,
    ) -> None:
        decision = str(dbg.get("decision") or "")
        cloud_verified = dbg.get("cloud_verified")
        cloud_outcome = dbg.get("cloud_outcome")

        if decision == "MATCHED":
            if cloud_verified:
                self._frame.recognition_source = "cloud"
            elif self._frame.recognition_source != "cloud":
                self._frame.recognition_source = "local"

        if decision == "OFFLOAD_TO_CLOUD" or cloud_outcome:
            rr = routing_reason or dbg.get("_routing_reason")
            mapped = map_offload_reason(
                rr,
                cloud_outcome=str(cloud_outcome) if cloud_outcome else None,
                frame_latency_ms=frame_latency_ms,
                latency_budget_ms=latency_budget_ms,
            )
            if mapped != "none" or self._frame.offload_reason == "none":
                self._frame.offload_reason = mapped
            if routing_should_offload or decision == "OFFLOAD_TO_CLOUD":
                if self._frame.offload_reason == "none":
                    self._frame.offload_reason = map_offload_reason(
                        rr,
                        cloud_outcome=str(cloud_outcome) if cloud_outcome else None,
                        frame_latency_ms=frame_latency_ms,
                        latency_budget_ms=latency_budget_ms,
                    )

    def apply_attendance_ingest(
        self,
        *,
        from_state: str | None = None,
        to_state: str | None = None,
        lecture_id: str | None = None,
        disposition: str | None = None,
        detail: str | None = None,
    ) -> None:
        if lecture_id or (disposition and "no_active_lecture" not in str(disposition)):
            self.lecture_active = bool(lecture_id) or self.lecture_active

        ts = to_state or from_state
        if ts:
            self.attendance_state = normalize_attendance_state(ts)
        elif detail:
            self.attendance_state = normalize_attendance_state(detail)
        elif disposition:
            disp = str(disposition).lower()
            if "insufficient" in disp:
                self.attendance_state = "insufficient_presence"
            elif disp in ("transitioned", "accepted"):
                pass

        transition = map_attendance_transition(from_state, to_state)
        if transition != "none":
            self.attendance_transition = transition
            self._frame.attendance_transition = transition

        self._frame.attendance_state = self.attendance_state
        self._frame.lecture_active = "true" if self.lecture_active else "false"

    def maybe_poll_cloud_presence(self, now: float | None = None) -> None:
        """Best-effort read of existing GET /health (no new cloud code)."""
        if not self._cloud_base_url:
            return
        t = now if now is not None else time.time()
        if t - self._last_health_poll < self.cloud_health_interval_s:
            return
        self._last_health_poll = t
        try:
            import requests

            resp = requests.get(
                f"{self._cloud_base_url}/health",
                timeout=1.5,
            )
            if resp.status_code != 200:
                return
            data = resp.json()
            counts = (data.get("attendance") or {}).get("counts") or {}
            sessions = int(counts.get("presence_sessions") or 0)
            self.surveillance_present = sessions > 0
        except Exception as exc:
            log.debug("Observability health poll skipped: %s", exc)

    def frame_snapshot(self) -> FrameObservability:
        self._frame.surveillance_present = (
            "true" if self.surveillance_present else "false"
        )
        self._frame.lecture_active = "true" if self.lecture_active else "false"
        self._frame.attendance_state = self.attendance_state
        if self._frame.attendance_transition == "none":
            self._frame.attendance_transition = self.attendance_transition
        return self._frame

    def frame_row_values(self) -> list[Any]:
        f = self.frame_snapshot()
        return [
            f.recognition_source,
            f.offload_reason,
            f.attendance_transition,
            f.surveillance_present,
            f.lecture_active,
            f.attendance_state,
        ]


def summarize_telemetry_columns(tel: Any) -> dict[str, Any]:
    """Aggregate unified metrics from a telemetry DataFrame (post-run)."""
    import pandas as pd

    out: dict[str, Any] = {
        "recognition_local_count": 0,
        "recognition_offload_count": 0,
        "attendance_confirmed": 0,
        "attendance_insufficient": 0,
        "surveillance_presence_ratio": 0.0,
        "fan_switch_count": 0,
        "thermal_time_low_sec": 0.0,
        "thermal_time_high_sec": 0.0,
    }
    if tel is None or len(tel) == 0:
        return out

    if "recognition_source" in tel.columns:
        src = tel["recognition_source"].astype(str).str.lower()
        out["recognition_local_count"] = int((src == "local").sum())
        out["recognition_offload_count"] = int((src == "cloud").sum())

    if "attendance_state" in tel.columns:
        ast = tel["attendance_state"].astype(str).str.lower()
        out["attendance_confirmed"] = int((ast == "confirmed").sum())
        out["attendance_insufficient"] = int((ast == "insufficient_presence").sum())

    if "surveillance_present" in tel.columns:
        sp = tel["surveillance_present"].astype(str).str.lower() == "true"
        out["surveillance_presence_ratio"] = float(sp.mean()) if len(sp) else 0.0

    if "fan_state" in tel.columns and "timestamp" in tel.columns:
        fs = tel["fan_state"].astype(str).str.upper()
        out["fan_switch_count"] = int((fs != fs.shift()).sum() - 1)
        out["fan_switch_count"] = max(0, out["fan_switch_count"])
        ts = tel["timestamp"].astype(float).diff().fillna(0)
        low_mask = fs.isin(["OFF", "LOW"])
        high_mask = fs.isin(["HIGH", "MAX"])
        out["thermal_time_low_sec"] = float(ts[low_mask].sum())
        out["thermal_time_high_sec"] = float(ts[high_mask].sum())

    return out


def compute_experiment_health_score(stats: dict[str, Any]) -> float:
    """Weighted health score in [0, 1]; post-run only."""
    local = float(stats.get("recognition_local_count") or 0)
    offload = float(stats.get("recognition_offload_count") or 0)
    rec_total = local + offload
    if rec_total > 0:
        recognition_score = local / rec_total
    else:
        recognition_score = 0.5

    p99 = float(stats.get("latency_total_ms_p99") or stats.get("latency_total_ms_mean") or 0)
    target = float(stats.get("latency_target_ms") or 150.0)
    if target <= 0:
        latency_score = 0.5
    else:
        latency_score = max(0.0, min(1.0, 1.0 - (p99 / (2.0 * target))))

    low_t = float(stats.get("thermal_time_low_sec") or 0)
    high_t = float(stats.get("thermal_time_high_sec") or 0)
    therm_denom = low_t + high_t
    if therm_denom > 0:
        thermal_score = low_t / therm_denom
    else:
        temp_max = float(stats.get("cpu_temp_max") or 0)
        if temp_max <= 0:
            thermal_score = 0.5
        else:
            thermal_score = max(0.0, min(1.0, 1.0 - max(0.0, temp_max - 65.0) / 20.0))

    confirmed = float(stats.get("attendance_confirmed") or 0)
    insufficient = float(stats.get("attendance_insufficient") or 0)
    att_denom = confirmed + insufficient
    if att_denom > 0:
        attendance_score = confirmed / att_denom
    else:
        attendance_score = 0.5

    health = (
        0.35 * recognition_score
        + 0.25 * latency_score
        + 0.20 * thermal_score
        + 0.20 * attendance_score
    )
    return round(max(0.0, min(1.0, health)), 4)
