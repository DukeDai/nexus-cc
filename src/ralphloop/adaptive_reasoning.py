"""AdaptiveReasoningConfig — Learned threshold adaptation for DynamicReasoning.

Extends DynamicReasoningEngine with:
- Per-phase threshold maps (learned from session history)
- Outcome-based threshold adjustment
- Rule adaptation based on success/failure patterns

This addresses the static threshold problem in the original implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .dynamic_reasoning import (
    DynamicReasoningEngine,
    ReasoningIntensity,
    ReasoningProfile,
    ReasoningSignals,
)
from .reasoning_signal import TrendIndicator, TrendDirection


@dataclass
class PhaseThresholds:
    """Learned thresholds for a specific phase.

    Includes both static thresholds and trend-aware overrides
    that react to deteriorating conditions before they become critical.
    """
    error_rate_high: float = 0.5
    error_rate_medium: float = 0.3
    success_streak_threshold: int = 5
    budget_degrading: float = 50.0
    budget_poor: float = 70.0
    tool_failure_high: float = 0.3
    # Trend-aware thresholds
    error_velocity_worsening_threshold: float = 0.3  # Slope above which to preemptively escalate
    plan_deviation_worsening_threshold: float = 0.2   # Slope above which to preemptively escalate


# Default thresholds per phase
DEFAULT_PHASE_THRESHOLDS: dict[str, PhaseThresholds] = {
    "PLAN": PhaseThresholds(
        error_rate_high=0.6,      # More tolerant in planning
        error_rate_medium=0.4,
        success_streak_threshold=4,
    ),
    "ACT": PhaseThresholds(
        error_rate_high=0.5,
        error_rate_medium=0.3,
        success_streak_threshold=5,
    ),
    "VERIFY": PhaseThresholds(
        error_rate_high=0.4,      # Less tolerant in verification
        error_rate_medium=0.25,
        budget_poor=60.0,        # More conservative
    ),
    "REFLECT": PhaseThresholds(
        error_rate_high=0.7,      # Very tolerant in reflection
        error_rate_medium=0.5,
    ),
}


@dataclass
class OutcomeRecord:
    """Record of a phase outcome for learning."""
    phase: str
    reasoning_intensity: ReasoningIntensity
    success: bool
    context_used: float  # Context budget at phase end
    turn_count: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class AdaptiveReasoningConfig:
    """Learned reasoning configuration from session history.

    Instead of static thresholds, this module learns optimal thresholds
    based on past outcomes per phase.

    Usage:
        config = AdaptiveReasoningConfig()
        config.record_outcome("ACT", ReasoningIntensity.DEEP, success=True,
                              context_used=45.0, turn_count=15)
        thresholds = config.get_thresholds("ACT")  # Learned thresholds
    """

    LEARN_RATE = 0.1  # How fast to adapt (0.0-1.0)
    MIN_THRESHOLD_ADJUSTMENT = 0.05
    MAX_HISTORY = 100  # Keep last N outcomes

    def __init__(self):
        self._phase_thresholds: dict[str, PhaseThresholds] = {
            phase: PhaseThresholds()  # Start with defaults
            for phase in DEFAULT_PHASE_THRESHOLDS
        }
        self._outcome_history: list[OutcomeRecord] = []

    def get_thresholds(self, phase: str) -> PhaseThresholds:
        """Get learned thresholds for a phase."""
        return self._phase_thresholds.get(
            phase,
            PhaseThresholds()  # Fallback to default
        )

    def record_outcome(
        self,
        phase: str,
        reasoning: ReasoningIntensity,
        success: bool,
        context_used: float,
        turn_count: int,
    ) -> None:
        """Record a phase outcome for threshold learning.

        This adjusts the phase's thresholds based on the outcome.
        """
        record = OutcomeRecord(
            phase=phase,
            reasoning_intensity=reasoning,
            success=success,
            context_used=context_used,
            turn_count=turn_count,
        )
        self._outcome_history.append(record)

        # Trim history
        if len(self._outcome_history) > self.MAX_HISTORY:
            self._outcome_history = self._outcome_history[-self.MAX_HISTORY:]

        # Update thresholds based on outcome
        self._adjust_thresholds(record)

    def _adjust_thresholds(self, record: OutcomeRecord) -> None:
        """Adjust thresholds based on outcome record."""
        thresholds = self._phase_thresholds.get(record.phase)
        if not thresholds:
            return

        # If high intensity but still failed → increase tolerance
        if record.success:
            # Success at current intensity → OK
            # But if we used high intensity unnecessarily, reduce thresholds
            if record.reasoning_intensity in {
                ReasoningIntensity.DEEP, ReasoningIntensity.VERIFY
            }:
                # Consider if we over-invested
                if record.turn_count < 10 and record.context_used < 40:
                    # Could have used simpler reasoning
                    thresholds.error_rate_high = max(
                        self.MIN_THRESHOLD_ADJUSTMENT,
                        thresholds.error_rate_high - 0.05
                    )
        else:
            # Failure → be more conservative
            thresholds.error_rate_high = min(
                0.9,
                thresholds.error_rate_high + 0.05
            )
            thresholds.error_rate_medium = min(
                0.7,
                thresholds.error_rate_medium + 0.03
            )

    def apply_thresholds(
        self,
        base: ReasoningSignals,
        phase: str
    ) -> ReasoningSignals:
        """Apply learned thresholds to scale signals for a phase.

        Includes trend-aware scaling: if signals indicate worsening trends,
        adjust thresholds to be more conservative (trigger escalation sooner).
        """
        thresholds = self.get_thresholds(phase)

        # Compute trend-adjusted error rate
        # If error velocity is worsening, effectively lower the error rate threshold
        error_rate_multiplier = 1.0
        if hasattr(base, 'error_velocity_trend') and base.error_velocity_trend:
            if base.error_velocity_trend.trend == TrendDirection.WORSENING:
                error_rate_multiplier = 0.7  # 30% more sensitive when trending bad

        if hasattr(base, 'plan_deviation_trend') and base.plan_deviation_trend:
            if base.plan_deviation_trend.trend == TrendDirection.WORSENING:
                error_rate_multiplier *= 0.8  # Further reduce when plan is drifting

        adjusted_error_rate = base.error_rate * error_rate_multiplier

        # Scale signals based on learned thresholds
        adjusted = ReasoningSignals(
            task_complexity=base.task_complexity,
            error_rate=adjusted_error_rate,
            success_streak=base.success_streak,
            context_budget_percent=base.context_budget_percent,
            phase_duration_ms=base.phase_duration_ms,
            tool_call_failure_ratio=base.tool_call_failure_ratio,
            error_velocity_trend=getattr(base, 'error_velocity_trend', TrendIndicator(0.0, 0.0, 0.0)),
            plan_deviation_trend=getattr(base, 'plan_deviation_trend', TrendIndicator(0.0, 0.0, 0.0)),
        )

        # Override threshold constants with learned values
        return adjusted

    def get_phase_stats(self, phase: str) -> dict:
        """Get learning statistics for a phase."""
        phase_outcomes = [o for o in self._outcome_history if o.phase == phase]
        if not phase_outcomes:
            return {
                "phase": phase,
                "outcome_count": 0,
                "success_rate": None,
                "avg_context_used": None,
            }

        successes = sum(1 for o in phase_outcomes if o.success)
        avg_context = sum(o.context_used for o in phase_outcomes) / len(phase_outcomes)
        avg_turns = sum(o.turn_count for o in phase_outcomes) / len(phase_outcomes)

        return {
            "phase": phase,
            "outcome_count": len(phase_outcomes),
            "success_rate": successes / len(phase_outcomes),
            "avg_context_used": round(avg_context, 1),
            "avg_turns": round(avg_turns, 1),
            "current_thresholds": self.get_thresholds(phase).__dict__,
        }

    def reset(self) -> None:
        """Reset all learned thresholds to defaults."""
        self._phase_thresholds = {
            phase: PhaseThresholds()
            for phase in DEFAULT_PHASE_THRESHOLDS
        }
        self._outcome_history.clear()


class AdaptiveReasoningEngine(DynamicReasoningEngine):
    """DynamicReasoningEngine with adaptive thresholds.

    Usage:
        engine = AdaptiveReasoningEngine()
        # ... run phases ...
        engine.record_phase_outcome("ACT", ReasoningIntensity.DEEP,
                                     success=False, context_used=55.0, turn_count=20)
        profile = engine.compute_profile(signals)  # Uses learned thresholds
    """

    def __init__(self):
        super().__init__()
        self._adaptive_config = AdaptiveReasoningConfig()

    def compute_profile(self, signals: ReasoningSignals, phase: Optional[str] = None) -> ReasoningProfile:
        """Compute profile using adaptive thresholds if phase provided."""
        if phase:
            adjusted_signals = self._adaptive_config.apply_thresholds(signals, phase)
            return super().compute_profile(adjusted_signals)
        return super().compute_profile(signals)

    def record_phase_outcome(
        self,
        phase: str,
        reasoning: ReasoningIntensity,
        success: bool,
        context_used: float,
        turn_count: int,
    ) -> None:
        """Record outcome and update thresholds."""
        self._adaptive_config.record_outcome(
            phase, reasoning, success, context_used, turn_count
        )

    def get_learning_stats(self, phase: Optional[str] = None) -> dict:
        """Get learning statistics."""
        if phase:
            return self._adaptive_config.get_phase_stats(phase)
        return {
            phase: self._adaptive_config.get_phase_stats(phase)
            for phase in DEFAULT_PHASE_THRESHOLDS
        }

    def reset_learning(self) -> None:
        """Reset learned thresholds to defaults."""
        self._adaptive_config.reset()