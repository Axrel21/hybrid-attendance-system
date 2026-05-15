"""
OffloadRouter — Confidence-based hybrid routing (Phase C3)

This is a MAJOR RESEARCH COMPONENT.

The offloading decision determines:
  - which frames consume cloud resources
  - offload frequency (key telemetry metric)
  - hybrid system accuracy
  - pipeline latency distribution

Three routing strategies are implemented to enable experiment comparison:
  1. ThresholdRouter      — static confidence threshold
  2. HysteresisRouter     — hysteresis band to reduce thrashing
  3. AdaptiveRouter       — sliding-window threshold adaptation

All routers share a common interface so they can be swapped in experiments
without touching the pipeline code.
"""

import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("offload_router")


# ── Decision types ────────────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    should_offload: bool
    reason: str                  # human-readable for logs
    confidence_at_decision: float
    strategy_name: str
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_telemetry_dict(self) -> dict:
        return {
            "routing_decision": "offload" if self.should_offload else "edge",
            "routing_reason": self.reason,
            "routing_confidence": self.confidence_at_decision,
            "routing_strategy": self.strategy_name,
        }


# ── Base interface ────────────────────────────────────────────────────────────

class BaseOffloadRouter(ABC):
    """Common interface for all routing strategies."""

    @abstractmethod
    def decide(
        self,
        edge_confidence: float,
        embedding: Optional[list] = None,
        extra: Optional[dict] = None,
    ) -> RoutingDecision:
        """
        Given edge confidence (and optionally the embedding or other metadata),
        decide whether to offload to cloud.

        Args:
            edge_confidence: Similarity score from edge MobileFaceNet [0, 1]
            embedding:       Raw embedding (may be used by future adaptive strategies)
            extra:           Additional context (liveness score, frame motion, etc.)

        Returns:
            RoutingDecision
        """
        pass

    @abstractmethod
    def stats(self) -> dict:
        """Return router telemetry/statistics for experiment reporting."""
        pass


# ── Strategy 1: Static threshold ─────────────────────────────────────────────

class ThresholdRouter(BaseOffloadRouter):
    """
    Simplest strategy: offload if edge_confidence < threshold.

    Experiment hypothesis:
      A fixed threshold that works well across all subjects and lighting
      conditions is unlikely — motivating the adaptive router.

    Parameters:
      threshold:     Confidence below which we offload (e.g. 0.65)
      force_offload: If True, ALL frames go to cloud (experiment baseline)
      force_edge:    If True, NO frames go to cloud (edge-only baseline)
    """

    def __init__(
        self,
        threshold: float = 0.65,
        force_offload: bool = False,
        force_edge: bool = False,
    ):
        self.threshold = threshold
        self.force_offload = force_offload
        self.force_edge = force_edge

        self._total_decisions = 0
        self._offload_count = 0

    def decide(self, edge_confidence: float, **kwargs) -> RoutingDecision:
        self._total_decisions += 1

        if self.force_edge:
            return RoutingDecision(
                should_offload=False,
                reason="force_edge_mode",
                confidence_at_decision=edge_confidence,
                strategy_name="threshold_forced_edge",
            )

        if self.force_offload:
            self._offload_count += 1
            return RoutingDecision(
                should_offload=True,
                reason="force_offload_mode",
                confidence_at_decision=edge_confidence,
                strategy_name="threshold_forced_offload",
            )

        should = edge_confidence < self.threshold
        if should:
            self._offload_count += 1

        return RoutingDecision(
            should_offload=should,
            reason=f"confidence {edge_confidence:.3f} {'<' if should else '>='} threshold {self.threshold:.3f}",
            confidence_at_decision=edge_confidence,
            strategy_name="threshold",
        )

    def stats(self) -> dict:
        return {
            "strategy": "threshold",
            "threshold": self.threshold,
            "total_decisions": self._total_decisions,
            "offload_count": self._offload_count,
            "offload_rate": self._offload_count / max(self._total_decisions, 1),
        }


# ── Strategy 2: Hysteresis band ───────────────────────────────────────────────

class HysteresisRouter(BaseOffloadRouter):
    """
    Reduces flip-flopping near the decision boundary with a dead band.

    Behaviour:
      confidence < low_threshold          → always offload
      confidence > high_threshold         → always edge
      low_threshold <= confidence <= high → continue previous decision

    Research motivation:
      Near-boundary frames often alternate offload/edge on consecutive frames
      for the same person. Hysteresis stabilises this.
    """

    def __init__(
        self,
        low_threshold: float = 0.60,
        high_threshold: float = 0.72,
    ):
        assert low_threshold < high_threshold, "low_threshold must be < high_threshold"
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold

        self._last_decision: bool = False   # default: edge
        self._total_decisions = 0
        self._offload_count = 0
        self._hysteresis_count = 0   # decisions made via hysteresis

    def decide(self, edge_confidence: float, **kwargs) -> RoutingDecision:
        self._total_decisions += 1

        if edge_confidence < self.low_threshold:
            decision = True
            reason = f"confidence {edge_confidence:.3f} < low_threshold {self.low_threshold:.3f}"
        elif edge_confidence > self.high_threshold:
            decision = False
            reason = f"confidence {edge_confidence:.3f} > high_threshold {self.high_threshold:.3f}"
        else:
            # In the dead band: carry previous decision
            decision = self._last_decision
            self._hysteresis_count += 1
            reason = (
                f"hysteresis band [{self.low_threshold:.3f}, {self.high_threshold:.3f}] "
                f"→ carry previous={'offload' if decision else 'edge'}"
            )

        self._last_decision = decision
        if decision:
            self._offload_count += 1

        return RoutingDecision(
            should_offload=decision,
            reason=reason,
            confidence_at_decision=edge_confidence,
            strategy_name="hysteresis",
        )

    def stats(self) -> dict:
        return {
            "strategy": "hysteresis",
            "low_threshold": self.low_threshold,
            "high_threshold": self.high_threshold,
            "total_decisions": self._total_decisions,
            "offload_count": self._offload_count,
            "offload_rate": self._offload_count / max(self._total_decisions, 1),
            "hysteresis_count": self._hysteresis_count,
            "hysteresis_rate": self._hysteresis_count / max(self._total_decisions, 1),
        }


# ── Strategy 3: Adaptive threshold ───────────────────────────────────────────

class AdaptiveRouter(BaseOffloadRouter):
    """
    Dynamically adjusts the threshold based on a sliding window of recent
    confidence scores and observed agreement between edge and cloud.

    Core idea:
      - Maintain a rolling window of recent confidence values
      - Compute local mean and std
      - Set threshold = mean - k*std  (adapt to current session distribution)
      - When edge/cloud disagree frequently → lower threshold (offload more)
      - When edge/cloud agree consistently → raise threshold (offload less)

    This is the most research-interesting strategy.
    Expected to show lower offload rate than static threshold in stable conditions.

    Parameters:
      initial_threshold:    Starting threshold before window fills
      window_size:          Number of recent frames for statistics
      k_sigma:              Threshold = mean - k * std
      target_offload_rate:  Optional: if set, adjust k to hit this rate
    """

    def __init__(
        self,
        initial_threshold: float = 0.65,
        window_size: int = 50,
        k_sigma: float = 1.0,
        min_threshold: float = 0.40,
        max_threshold: float = 0.85,
        agreement_feedback: bool = True,
    ):
        self.initial_threshold = initial_threshold
        self.window_size = window_size
        self.k_sigma = k_sigma
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        self.agreement_feedback = agreement_feedback

        self._current_threshold = initial_threshold
        self._confidence_window: deque = deque(maxlen=window_size)
        self._agreement_window: deque = deque(maxlen=20)   # recent agree/disagree

        self._total_decisions = 0
        self._offload_count = 0
        self._threshold_history: list = []   # for plotting

    def decide(self, edge_confidence: float, **kwargs) -> RoutingDecision:
        self._total_decisions += 1
        self._confidence_window.append(edge_confidence)

        # Recompute threshold if window has enough data
        if len(self._confidence_window) >= 10:
            self._recompute_threshold()

        should_offload = edge_confidence < self._current_threshold
        if should_offload:
            self._offload_count += 1

        reason = (
            f"confidence {edge_confidence:.3f} {'<' if should_offload else '>='} "
            f"adaptive_threshold {self._current_threshold:.3f} "
            f"(window_n={len(self._confidence_window)})"
        )

        return RoutingDecision(
            should_offload=should_offload,
            reason=reason,
            confidence_at_decision=edge_confidence,
            strategy_name="adaptive",
        )

    def feedback(self, agreed: bool):
        """
        Call after a cloud verification to update the agreement signal.
        Allows the router to self-calibrate based on observed edge/cloud divergence.

        agreed=True:  edge and cloud agreed → edge performing well → raise threshold
        agreed=False: disagreement → edge struggling → lower threshold
        """
        if not self.agreement_feedback:
            return
        self._agreement_window.append(agreed)
        self._recompute_threshold()

    def _recompute_threshold(self):
        import numpy as np

        scores = list(self._confidence_window)
        mean = float(np.mean(scores))
        std = float(np.std(scores))

        # Base: mean - k*std (captures the lower tail of the session distribution)
        base = mean - self.k_sigma * std

        # Agreement feedback adjustment
        if len(self._agreement_window) >= 5:
            agree_rate = sum(self._agreement_window) / len(self._agreement_window)
            if agree_rate > 0.9:
                # Very high agreement: can be more aggressive (raise threshold)
                base *= 0.95
            elif agree_rate < 0.6:
                # Frequent disagreement: lower threshold (offload more)
                base *= 1.08

        self._current_threshold = float(np.clip(base, self.min_threshold, self.max_threshold))
        self._threshold_history.append(self._current_threshold)

    def stats(self) -> dict:
        return {
            "strategy": "adaptive",
            "current_threshold": self._current_threshold,
            "initial_threshold": self.initial_threshold,
            "window_size": self.window_size,
            "k_sigma": self.k_sigma,
            "total_decisions": self._total_decisions,
            "offload_count": self._offload_count,
            "offload_rate": self._offload_count / max(self._total_decisions, 1),
            "agreement_feedback_enabled": self.agreement_feedback,
            "recent_agreement_rate": (
                sum(self._agreement_window) / max(len(self._agreement_window), 1)
            ),
        }


# ── Factory ───────────────────────────────────────────────────────────────────

def create_router(strategy: str, **kwargs) -> BaseOffloadRouter:
    """
    Instantiate a routing strategy by name.

    Experiment config usage:
        router = create_router("threshold", threshold=0.65)
        router = create_router("hysteresis", low_threshold=0.58, high_threshold=0.72)
        router = create_router("adaptive", window_size=50, k_sigma=1.2)
        router = create_router("threshold", force_offload=True)   # all-cloud baseline
        router = create_router("threshold", force_edge=True)       # all-edge baseline
    """
    strategies = {
        "threshold": ThresholdRouter,
        "hysteresis": HysteresisRouter,
        "adaptive": AdaptiveRouter,
    }
    cls = strategies.get(strategy)
    if cls is None:
        raise ValueError(f"Unknown routing strategy '{strategy}'. Options: {list(strategies.keys())}")
    return cls(**kwargs)
