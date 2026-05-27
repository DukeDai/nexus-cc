"""ReasoningSignal — Signal types for DynamicReasoningEngine integration.

Defines the signal schema that FeedbackLoop uses to drive reasoning adjustments.
Includes trend analysis for predictive reasoning (forward-looking, not just reactive).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional


class TrendDirection(Enum):
    """Direction of a metric's trend."""
    WORSENING = auto()
    STABLE = auto()
    IMPROVING = auto()


@dataclass
class TrendIndicator:
    """A metric with its current value, trend direction, and acceleration."""
    current: float
    slope: float  # Rate of change per sample
    acceleration: float  # Second derivative (change in slope)
    trend: TrendDirection = TrendDirection.STABLE

    @staticmethod
    def compute(values: list[float]) -> "TrendIndicator":
        """Compute trend from a list of values (oldest first).

        Uses linear regression for slope and second difference for acceleration.
        """
        if len(values) < 2:
            return TrendIndicator(current=values[-1] if values else 0.0, slope=0.0, acceleration=0.0)

        n = len(values)
        current = values[-1]

        # Linear regression for slope
        x_vals = list(range(n))
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, values))
        denominator = sum((x - x_mean) ** 2 for x in x_vals)

        slope = numerator / denominator if denominator != 0 else 0.0

        # Acceleration: second difference
        if n >= 3:
            acceleration = values[-1] - 2 * values[-2] + values[-3]
        else:
            acceleration = 0.0

        # Determine trend direction
        if slope > 0.05:  # Positive slope = worsening for error rates
            trend = TrendDirection.WORSENING
        elif slope < -0.05:
            trend = TrendDirection.IMPROVING
        else:
            trend = TrendDirection.STABLE

        return TrendIndicator(
            current=current,
            slope=slope,
            acceleration=acceleration,
            trend=trend
        )


@dataclass
class ReasoningSignal:
    """Observable signals that drive reasoning intensity adjustment.

    These are computed from FeedbackLoop events and fed to
    DynamicReasoningEngine for profile computation.

    Attributes:
        plan_deviation: How much actual execution diverged from plan (0.0-1.0)
        error_velocity: Rate of error production (errors per minute)
        context_burn_rate: How fast context budget is consumed (% per phase)
        phase_type: Current phase for threshold lookup
        task_complexity_override: Override for complexity-based scaling
        error_velocity_trend: Trend direction for error velocity (predictive)
        plan_deviation_trend: Trend direction for plan deviation (predictive)
    """
    plan_deviation: float = 0.0          # 0.0 = on track, 1.0 = totally off plan
    error_velocity: float = 0.0         # errors/minute
    context_burn_rate: float = 0.0      # % per phase
    phase_type: str = ""                # For phase-specific thresholds
    task_complexity_override: str = ""  # SIMPLE/MODERATE/COMPLEX override
    # Trend indicators for predictive reasoning
    error_velocity_trend: TrendIndicator = field(default_factory=lambda: TrendIndicator(0.0, 0.0, 0.0))
    plan_deviation_trend: TrendIndicator = field(default_factory=lambda: TrendIndicator(0.0, 0.0, 0.0))

    @property
    def is_on_track(self) -> bool:
        """True if execution is following the plan."""
        return self.plan_deviation < 0.3

    def should_preemptively_escalate(self) -> bool:
        """Predictive check: should we escalate before problems worsen?

        This is the key predictive reasoning method. If error_velocity
        AND plan_deviation are both worsening, we're heading toward trouble.
        """
        return (
            self.error_velocity_trend.trend == TrendDirection.WORSENING and
            self.plan_deviation_trend.trend == TrendDirection.WORSENING
        )

    def to_dict(self) -> dict:
        return {
            "plan_deviation": self.plan_deviation,
            "error_velocity": self.error_velocity,
            "context_burn_rate": self.context_burn_rate,
            "phase_type": self.phase_type,
            "error_velocity_trend": self.error_velocity_trend.trend.name,
            "plan_deviation_trend": self.plan_deviation_trend.trend.name,
        }


class SignalComputer:
    """Computes ReasoningSignals from FeedbackLoop events.

    Computes both current values AND trends for predictive reasoning.
    Trends allow forward-looking adjustments, not just reactive.

    Usage:
        computer = SignalComputer()
        computer.record_event(FeedbackEvent(type=TASK_FAILED, ...))
        computer.record_event(FeedbackEvent(type=CONTEXT_DEGRADING, ...))
        signal = computer.compute()  # Get aggregated signal with trends
    """

    def __init__(self, window_seconds: float = 300.0, min_sample_size: int = 5):
        self.window_seconds = window_seconds
        self.min_sample_size = min_sample_size
        self._events: list[tuple[datetime, str]] = []  # (timestamp, event_type)
        self._phase_durations: list[tuple[datetime, float]] = []  # (timestamp, duration_ms)
        self._context_samples: list[tuple[datetime, float]] = []  # (timestamp, budget_percent)
        self._plan_actual_pairs: list[tuple[str, str]] = []  # (planned, actual)
        # Per-type event counts for trend analysis
        self._event_type_counts: list[tuple[datetime, dict[str, int]]] = []  # Rolling window of event counts

    def record_event(self, event_type: str) -> None:
        """Record an event for signal computation."""
        self._events.append((datetime.now(), event_type))

    def record_event_batch(self, event_counts: dict[str, int]) -> None:
        """Record a batch of event counts for trend analysis.

        This is more efficient than recording individual events when
        the FeedbackLoop already aggregates counts.
        """
        self._event_type_counts.append((datetime.now(), event_counts.copy()))

    def record_phase_duration(self, duration_ms: float) -> None:
        """Record a phase duration sample."""
        self._phase_durations.append((datetime.now(), duration_ms))

    def record_context_sample(self, budget_percent: float) -> None:
        """Record a context budget sample."""
        self._context_samples.append((datetime.now(), budget_percent))

    def record_plan_actual(self, planned: str, actual: str) -> None:
        """Record a plan vs actual pair for deviation calculation."""
        self._plan_actual_pairs.append((planned, actual))

    def _compute_error_velocity_trend(self) -> TrendIndicator:
        """Compute trend for error velocity over time window."""
        # Build time-series of error events per time slice
        now = datetime.now()
        cutoff = now.timestamp() - self.window_seconds

        # Group events by time bucket (1 minute buckets)
        buckets: dict[int, int] = {}
        for ts, et in self._events:
            if ts.timestamp() >= cutoff:
                bucket = int((ts.timestamp() - cutoff) / 60)  # 1-min buckets
                if "FAILED" in et or "ERROR" in et:
                    buckets[bucket] = buckets.get(bucket, 0) + 1

        if len(buckets) < 2:
            return TrendIndicator(0.0, 0.0, 0.0)

        # Convert to ordered list
        sorted_buckets = sorted(buckets.items())
        values = [count for _, count in sorted_buckets]

        return TrendIndicator.compute(values)

    def _compute_plan_deviation_trend(self) -> TrendIndicator:
        """Compute trend for plan deviation over recent executions."""
        if not self._plan_actual_pairs:
            return TrendIndicator(0.0, 0.0, 0.0)

        # Compute deviation per pair
        deviations = []
        for planned, actual in self._plan_actual_pairs[-20:]:  # Last 20
            if planned != actual:
                deviations.append(0.3)
            else:
                deviations.append(0.0)

        if len(deviations) < 2:
            return TrendIndicator(current=deviations[-1] if deviations else 0.0, slope=0.0, acceleration=0.0)

        return TrendIndicator.compute(deviations)

    def compute(self) -> ReasoningSignal:
        """Compute the aggregated signal with trends for predictive reasoning."""
        now = datetime.now()

        # Filter to window
        cutoff = now.timestamp() - self.window_seconds
        recent_events = [
            (ts, et) for ts, et in self._events
            if ts.timestamp() >= cutoff
        ]

        # Compute error velocity (events per minute)
        error_events = [et for _, et in recent_events if "FAILED" in et or "ERROR" in et]
        error_velocity = (len(error_events) / self.window_seconds) * 60.0

        # Compute context burn rate (% per phase)
        recent_context = [
            (ts, bp) for ts, bp in self._context_samples
            if ts.timestamp() >= cutoff
        ]
        if len(recent_context) >= 2:
            oldest = recent_context[0][1]
            newest = recent_context[-1][1]
            phases_in_window = len(recent_context)
            if phases_in_window > 0:
                context_burn_rate = (newest - oldest) / phases_in_window
            else:
                context_burn_rate = 0.0
        else:
            context_burn_rate = 0.0

        # Compute plan deviation
        plan_deviation = self._compute_deviation()

        # Compute trends for predictive reasoning
        error_velocity_trend = self._compute_error_velocity_trend()
        plan_deviation_trend = self._compute_plan_deviation_trend()

        return ReasoningSignal(
            plan_deviation=min(1.0, plan_deviation),
            error_velocity=min(10.0, error_velocity),  # Cap at 10/min
            context_burn_rate=max(0.0, context_burn_rate),
            error_velocity_trend=error_velocity_trend,
            plan_deviation_trend=plan_deviation_trend,
        )

    def _compute_deviation(self) -> float:
        """Compute plan vs actual deviation.

        Uses semantic comparison: exact match=0, equivalent=0.1, different=0.5.
        """
        if not self._plan_actual_pairs:
            return 0.0

        def _semantic_diff(planned: str, actual: str) -> float:
            """Estimate deviation magnitude between planned and actual."""
            if planned == actual:
                return 0.0
            # Normalize and compare
            p_norm = planned.lower().strip()
            a_norm = actual.lower().strip()
            if p_norm == a_norm:
                return 0.0
            # Check prefix match (same task, different version)
            if p_norm.split()[0] == a_norm.split()[0] if p_norm and a_norm else False:
                return 0.1
            # Check substring containment
            if len(p_norm) > 5 and (p_norm in a_norm or a_norm in p_norm):
                return 0.2
            return 0.5  # Meaningfully different plans

        deviations = [
            _semantic_diff(planned, actual)
            for planned, actual in self._plan_actual_pairs[-10:]
        ]
        return sum(deviations) / len(deviations) if deviations else 0.0

    def clear(self) -> None:
        """Clear all recorded data."""
        self._events.clear()
        self._phase_durations.clear()
        self._context_samples.clear()
        self._plan_actual_pairs.clear()