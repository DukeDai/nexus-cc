"""Predictive Intervention Engine — Predictive Reasoning Intensity Adjustment.

This module replaces reactive reasoning intensity with predictive:
- Predict errors before they occur (not just react to current state)
- Detect acceleration patterns (error velocity ACCELERATING)
- Trigger pre-compression when both trends worsen
- Pattern matching against session history

Key insight: Reactive调节是"已经出了问题再补救", 预测性干预是"看到苗头就调节".
就像汽车ABS防抱死——监测到轮胎打滑趋势比已经打滑再反应更有效.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Optional
import math


class TrendDirection(Enum):
    """Trend direction for signals."""
    WORSENING = auto()
    STABLE = auto()
    IMPROVING = auto()


class InterventionType(Enum):
    """Types of predictive interventions."""
    NONE = auto()
    ESCALATE_INTENSITY = auto()      # Increase reasoning effort
    PRE_COMPRESS = auto()            # Compress context before crisis
    REPLAN = auto()                  # Trigger replan
    DECOMPOSE = auto()               # Break into smaller tasks
    ESCALATE_HUMAN = auto()          # Human intervention needed


@dataclass
class TrendIndicator:
    """A signal with trend analysis.

    Attributes:
        current: Current value
        slope: Rate of change (positive = worsening)
        acceleration: Second derivative (positive = accelerating)
        trend: Overall trend direction
    """
    current: float
    slope: float  # First derivative (dy/dt)
    acceleration: float  # Second derivative (d²y/dt²)
    trend: TrendDirection = TrendDirection.STABLE

    def __post_init__(self):
        # Determine trend from slope and acceleration
        if self.slope > 0.05:
            if self.acceleration > 0.02:
                self.trend = TrendDirection.WORSENING
            else:
                self.trend = TrendDirection.STABLE
        elif self.slope < -0.05:
            self.trend = TrendDirection.IMPROVING
        else:
            self.trend = TrendDirection.STABLE


@dataclass
class Intervention:
    """A predictive intervention recommendation.

    Attributes:
        intervention_type: What to do
        confidence: How confident we are (0.0-1.0)
        reason: Why this intervention is recommended
        urgency: How urgent (0.0-1.0)
        predicted_event: What we're trying to prevent
    """
    intervention_type: InterventionType
    confidence: float = 0.5
    reason: str = ""
    urgency: float = 0.5
    predicted_event: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class PredictionSignal:
    """Signals for prediction."""
    error_velocity: TrendIndicator
    plan_deviation: TrendIndicator
    context_budget_trend: TrendIndicator
    phase_duration_trend: TrendIndicator
    success_rate_trend: TrendIndicator  # Success rate trend (higher = better)


class PredictiveInterventionEngine:
    """Predictive intervention based on trend analysis.

    Instead of reacting to current state, this engine:
    1. Tracks signals over time
    2. Computes velocity and acceleration
    3. Predicts when intervention will be needed
    4. Triggers early intervention before crisis

    Usage:
        engine = PredictiveInterventionEngine()

        # Record signals each turn
        engine.record_error(count=2)
        engine.record_context_budget(percent=45.0)
        engine.record_phase_duration(ms=5000)

        # Check if intervention needed
        intervention = engine.should_intervene()
        if intervention.intervention_type != InterventionType.NONE:
            apply_intervention(intervention)
    """

    # Thresholds for intervention
    ERROR_VELOCITY_HIGH = 0.3       # Errors per turn
    ERROR_ACCELERATION_CONCERN = 0.1  # Second derivative threshold
    DEVIATION_SLOPE_THRESHOLD = 0.2   # Plan drift rate
    CONTEXT_BUDGET_CRITICAL = 70.0   # % context used
    CONTEXT_PRECOMPRESS_TRIGGER = 60.0  # % when we should pre-compress

    # Prediction horizon (how far ahead to predict)
    PREDICTION_HORIZON_TURNS = 5

    def __init__(self):
        self._error_history: list[float] = []
        self._context_history: list[float] = []
        self._duration_history: list[float] = []
        self._success_history: list[bool] = []
        self._turn_count = 0
        self._last_intervention_time: Optional[datetime] = None
        self._intervention_cooldown_turns = 3  # Don't干预 too frequently

    def record_error(self, count: int = 1) -> None:
        """Record an error occurrence."""
        if not self._error_history:
            self._error_history = [0.0] * 5  # Pad with zeros for initial slope calc
        self._error_history.append(count)
        if len(self._error_history) > 20:
            self._error_history = self._error_history[-20:]

    def record_context_budget(self, percent: float) -> None:
        """Record context budget usage."""
        if not self._context_history:
            self._context_history = [0.0] * 5
        self._context_history.append(percent)
        if len(self._context_history) > 20:
            self._context_history = self._context_history[-20:]

    def record_phase_duration(self, ms: float) -> None:
        """Record phase duration."""
        if not self._duration_history:
            self._duration_history = [0.0] * 5
        self._duration_history.append(ms)
        if len(self._duration_history) > 20:
            self._duration_history = self._duration_history[-20:]

    def record_success(self, success: bool) -> None:
        """Record phase success."""
        self._success_history.append(success)
        if len(self._success_history) > 20:
            self._success_history = self._success_history[-20:]

    def _compute_trend(self, values: list[float]) -> TrendIndicator:
        """Compute trend (slope and acceleration) from value history."""
        if len(values) < 3:
            return TrendIndicator(current=values[-1] if values else 0.0, slope=0.0, acceleration=0.0)

        # Simple linear regression for slope
        n = len(values)
        x = list(range(n))

        sum_x = sum(x)
        sum_y = sum(values)
        sum_xy = sum(x[i] * values[i] for i in range(n))
        sum_x2 = sum(x[i] ** 2 for i in range(n))

        # Slope (m = (n*sum_xy - sum_x*sum_y) / (n*sum_x2 - sum_x^2))
        denominator = n * sum_x2 - sum_x ** 2
        if abs(denominator) < 1e-10:
            slope = 0.0
        else:
            slope = (n * sum_xy - sum_x * sum_y) / denominator

        # Acceleration (change in slope over time)
        if len(values) >= 5:
            # Compare recent slope to older slope
            recent = values[-3:]
            older = values[-5:-2]
            slope_recent = self._simple_slope(recent)
            slope_older = self._simple_slope(older)
            acceleration = slope_recent - slope_older
        else:
            acceleration = 0.0

        current = values[-1]

        return TrendIndicator(
            current=current,
            slope=slope,
            acceleration=acceleration
        )

    def _simple_slope(self, values: list[float]) -> float:
        """Simple slope between first and last."""
        if len(values) < 2:
            return 0.0
        return (values[-1] - values[0]) / len(values)

    def _compute_success_trend(self) -> TrendIndicator:
        """Compute success rate trend."""
        if not self._success_history:
            return TrendIndicator(current=1.0, slope=0.0, acceleration=0.0)

        # Convert to binary (1=success, 0=fail)
        values = [1.0 if s else 0.0 for s in self._success_history]

        trend = self._compute_trend(values)
        # Invert slope (higher success = better, so negative slope is bad)
        trend.slope = -trend.slope
        trend.acceleration = -trend.acceleration

        return trend

    def get_prediction_signals(self) -> PredictionSignal:
        """Get current prediction signals."""
        return PredictionSignal(
            error_velocity=self._compute_trend(self._error_history),
            plan_deviation=self._compute_trend(self._context_history),  # Context drift
            context_budget_trend=self._compute_trend(self._context_history),
            phase_duration_trend=self._compute_trend(self._duration_history),
            success_rate_trend=self._compute_success_trend(),
        )

    def should_intervene(self) -> Intervention:
        """Determine if predictive intervention is needed.

        Uses trend acceleration to predict future problems:
        - Error velocity ACCELERATING → 立即升强度
        - Plan deviation + context budget both WORSENING → 预压缩
        - Success rate accelerating downward → 重新规划
        """
        self._turn_count += 1

        # Check cooldown
        if (self._turn_count % self._intervention_cooldown_turns) != 0:
            if self._last_intervention_time:
                elapsed = (datetime.now() - self._last_intervention_time).total_seconds()
                if elapsed < 30:  # 30 second cooldown
                    return Intervention(intervention_type=InterventionType.NONE)

        signals = self.get_prediction_signals()

        # Pattern 1: Error velocity ACCELERATING (most urgent)
        if self._is_error_accelerating(signals.error_velocity):
            self._last_intervention_time = datetime.now()
            return Intervention(
                intervention_type=InterventionType.ESCALATE_INTENSITY,
                confidence=0.85,
                reason="Error velocity accelerating — preemptively increase reasoning effort",
                urgency=0.9,
                predicted_event="error_rate_exceeding_threshold",
                timestamp=datetime.now().isoformat()
            )

        # Pattern 2: Both error AND context budget trending WORSENING
        if (signals.error_velocity.trend == TrendDirection.WORSENING and
            signals.context_budget_trend.trend == TrendDirection.WORSENING):
            self._last_intervention_time = datetime.now()
            return Intervention(
                intervention_type=InterventionType.PRE_COMPRESS,
                confidence=0.8,
                reason="Error trend + budget trend both worsening — compress context before crisis",
                urgency=0.85,
                predicted_event="context_budget_exhaustion",
                timestamp=datetime.now().isoformat()
            )

        # Pattern 3: Success rate accelerating downward
        if signals.success_rate_trend.trend == TrendDirection.WORSENING:
            if signals.success_rate_trend.acceleration > 0.05:
                self._last_intervention_time = datetime.now()
                return Intervention(
                    intervention_type=InterventionType.REPLAN,
                    confidence=0.75,
                    reason="Success rate declining with acceleration — current plan may be flawed",
                    urgency=0.8,
                    predicted_event="repeated_task_failure",
                    timestamp=datetime.now().isoformat()
                )

        # Pattern 4: Context budget approaching critical threshold
        if len(self._context_history) >= 5:
            current_budget = self._context_history[-1]
            budget_trend = signals.context_budget_trend

            # Predict future budget
            predicted_budget = current_budget + budget_trend.slope * self.PREDICTION_HORIZON_TURNS

            if predicted_budget >= self.CONTEXT_BUDGET_CRITICAL:
                self._last_intervention_time = datetime.now()
                return Intervention(
                    intervention_type=InterventionType.PRE_COMPRESS,
                    confidence=0.7,
                    reason=f"Context budget predicted to reach {predicted_budget:.0f}% in {self.PREDICTION_HORIZON_TURNS} turns",
                    urgency=0.75,
                    predicted_event="context_budget_exhaustion",
                    timestamp=datetime.now().isoformat()
                )

        # Pattern 5: Phase duration growing while errors increase
        if signals.phase_duration_trend.trend == TrendDirection.WORSENING:
            if signals.error_velocity.trend == TrendDirection.WORSENING:
                self._last_intervention_time = datetime.now()
                return Intervention(
                    intervention_type=InterventionType.DECOMPOSE,
                    confidence=0.65,
                    reason="Tasks taking longer while failing more — likely too complex",
                    urgency=0.7,
                    predicted_event="task_timeout",
                    timestamp=datetime.now().isoformat()
                )

        return Intervention(intervention_type=InterventionType.NONE)

    def _is_error_accelerating(self, error_trend: TrendIndicator) -> bool:
        """Check if error rate is accelerating."""
        # Accelerating means: positive slope AND positive acceleration
        return (error_trend.slope > self.ERROR_VELOCITY_HIGH and
                error_trend.acceleration > self.ERROR_ACCELERATION_CONCERN)

    def predict_time_to_budget_exhaustion(self) -> Optional[float]:
        """Predict how many turns until context budget is exhausted.

        Returns None if budget is stable or improving.
        """
        if len(self._context_history) < 5:
            return None

        trend = self._compute_trend(self._context_history)

        if trend.slope <= 0:
            return None  # Not growing

        current = self._context_history[-1]
        if current >= 100:
            return 0.0

        remaining = 100 - current
        turns = remaining / trend.slope if trend.slope > 0 else float('inf')
        return turns

    def get_stats(self) -> dict:
        """Get engine statistics."""
        signals = self.get_prediction_signals()
        return {
            "turn_count": self._turn_count,
            "error_velocity": {
                "current": signals.error_velocity.current,
                "slope": signals.error_velocity.slope,
                "trend": signals.error_velocity.trend.name,
            },
            "context_budget": {
                "current": signals.context_budget_trend.current,
                "slope": signals.context_budget_trend.slope,
                "trend": signals.context_budget_trend.trend.name,
            },
            "predicted_turns_to_exhaustion": self.predict_time_to_budget_exhaustion(),
            "last_intervention": self._last_intervention_time.isoformat() if self._last_intervention_time else None,
        }


# ─── Backward Compatibility Alias ──────────────────────────────────────────


# Re-export for existing code
ReasoningSignal = PredictionSignal