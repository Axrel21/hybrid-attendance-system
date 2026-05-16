# edge/stabilization.py
"""Minimal, opt-in, telemetry-backed runtime stabilizers.

Every class in this module is defensive about its inputs and *no-ops*
when its controlling configuration knob is at the default value. The
intent is "set BBOX_EMA_ALPHA=0.30 and try a run" rather than a
permanent runtime behaviour change.

Per-track state is held in plain dicts. Edge/main.py must call
``reset(track_id)`` (or ``reset_all()``) when tracks expire so state
doesn't grow unbounded.

Reference: ``docs/runtime_stabilization_phase_summary.md`` for the
behavioural evidence behind each helper.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple


# ── BBox EMA ──────────────────────────────────────────────────────────────────

class BBoxEMASmoother:
    """Per-track exponential moving average over (x, y, w, h).

    The first frame for a track passes through untouched (smoothed ==
    raw). Subsequent frames apply ``new = alpha * raw + (1 - alpha) *
    prev`` per dimension. ``alpha == 0`` is treated as disabled and
    each call passes the input through.
    """

    def __init__(self, alpha: float = 0.0) -> None:
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self._state: Dict[Any, Tuple[float, float, float, float]] = {}

    @property
    def enabled(self) -> bool:
        return self.alpha > 0.0

    def smooth(self, track_id: Any, box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        if not self.enabled:
            return box
        x, y, w, h = (float(v) for v in box)
        prev = self._state.get(track_id)
        if prev is None:
            sx, sy, sw, sh = x, y, w, h
        else:
            a = self.alpha
            sx = a * x + (1.0 - a) * prev[0]
            sy = a * y + (1.0 - a) * prev[1]
            sw = a * w + (1.0 - a) * prev[2]
            sh = a * h + (1.0 - a) * prev[3]
        self._state[track_id] = (sx, sy, sw, sh)
        # Cast back to int so downstream consumers (cropping, IoU) stay happy.
        return int(round(sx)), int(round(sy)), int(round(sw)), int(round(sh))

    def reset(self, track_id: Any) -> None:
        self._state.pop(track_id, None)

    def reset_all(self) -> None:
        self._state.clear()


# ── Similarity EMA ───────────────────────────────────────────────────────────

class SimEMASmoother:
    """Per-track EMA over the recognition similarity score.

    ``alpha == 0`` is disabled — the raw value is returned. The first
    value for a track is taken verbatim so a single-frame transient
    doesn't bias the running estimate.
    """

    def __init__(self, alpha: float = 0.0) -> None:
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self._state: Dict[Any, float] = {}

    @property
    def enabled(self) -> bool:
        return self.alpha > 0.0

    def smooth(self, track_id: Any, sim: float) -> float:
        if not self.enabled:
            return float(sim)
        prev = self._state.get(track_id)
        if prev is None:
            s = float(sim)
        else:
            a = self.alpha
            s = a * float(sim) + (1.0 - a) * prev
        self._state[track_id] = s
        return s

    def reset(self, track_id: Any) -> None:
        self._state.pop(track_id, None)

    def reset_all(self) -> None:
        self._state.clear()


# ── Match-persistence counter ────────────────────────────────────────────────

class MatchPersistenceCounter:
    """Per-track ``(identity → consecutive frames matched)`` counter.

    ``required == 1`` is the current runtime behaviour: a single
    MATCHED frame is enough. Higher values delay the "first attendance
    log" event until the same identity has been MATCHED for N
    consecutive frames.
    """

    def __init__(self, required: int = 1) -> None:
        self.required = max(1, int(required))
        # track_id -> (identity, run_length)
        self._state: Dict[Any, Tuple[str, int]] = {}

    @property
    def enabled(self) -> bool:
        return self.required > 1

    def observe(self, track_id: Any, identity: str) -> Tuple[int, bool]:
        """Record a MATCHED frame; return ``(run_length, sufficient)``.

        ``sufficient`` is True the first frame the run reaches ``required``
        and then stays True until the identity changes / the track is
        reset. Use ``sufficient`` to gate the attendance log row.
        """
        prev = self._state.get(track_id)
        if prev is None or prev[0] != identity:
            run = 1
        else:
            run = prev[1] + 1
        self._state[track_id] = (identity, run)
        return run, run >= self.required

    def reset(self, track_id: Any) -> None:
        self._state.pop(track_id, None)

    def reset_all(self) -> None:
        self._state.clear()


# ── PAD spoof-streak smoother ────────────────────────────────────────────────

class PADSpoofStreakSmoother:
    """Damp single-frame SPOOF transients via a per-track streak counter.

    Returns the original label unless the liveness engine just reported
    ``SPOOF`` and the running streak hasn't yet reached the configured
    minimum. While the streak is below threshold, the label is downgraded
    to ``UNCERTAIN`` so the pipeline doesn't reject the track on a
    one-frame motion glitch.

    Defaults (``required == 1``) preserve the historic behaviour.
    """

    def __init__(self, required: int = 1) -> None:
        self.required = max(1, int(required))
        self._spoof_streak: Dict[Any, int] = {}

    @property
    def enabled(self) -> bool:
        return self.required > 1

    def smooth(self, track_id: Any, lbl: str) -> str:
        if not self.enabled:
            return lbl
        if lbl == "SPOOF":
            streak = self._spoof_streak.get(track_id, 0) + 1
            self._spoof_streak[track_id] = streak
            if streak < self.required:
                return "UNCERTAIN"
            return "SPOOF"
        # Non-SPOOF label resets the streak.
        self._spoof_streak.pop(track_id, None)
        return lbl

    def reset(self, track_id: Any) -> None:
        self._spoof_streak.pop(track_id, None)

    def reset_all(self) -> None:
        self._spoof_streak.clear()


__all__ = [
    "BBoxEMASmoother",
    "SimEMASmoother",
    "MatchPersistenceCounter",
    "PADSpoofStreakSmoother",
]
