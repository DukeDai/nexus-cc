"""Dynamic Reasoning — Adaptive Reasoning Intensity Based on Task/Signals.

This module implements adaptive reasoning effort:
- Simple tasks → MINIMAL intensity (fast, single pass)
- Complex tasks → DEEP intensity (more iterations, reflection)
- Error signals → increase intensity for better recovery
- Success signals → decrease intensity for efficiency

Key insight: Not all tasks need the same reasoning effort.
Dynamic adjustment saves tokens on simple tasks while
investing more in complex ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from .reasoning_signal import TrendIndicator, TrendDirection
from typing import Optional


class ReasoningIntensity(Enum):
    """Reasoning effort levels, ordered from least to most."""
    MINIMAL = auto()   # Fast, single pass, minimal reflection
    MODERATE = auto()  # Standard loop, normal checkpoints
    DEEP = auto()      # More iterations, explicit reflection phase
    VERIFY = auto()    # Extra verification pass, double-check work


@dataclass
class ReasoningProfile:
    """Configuration for reasoning effort.

    Attributes:
        intensity: Current reasoning intensity level
        max_turns: Maximum LLM turns per phase
        checkpoint_interval: How often to checkpoint (lower = more safety)
        allow_parallel: Whether to allow parallel task execution
        enable_reflection: Whether to run explicit reflection phase
    """
    intensity: ReasoningIntensity = ReasoningIntensity.MODERATE
    max_turns: int = 20
    checkpoint_interval: int = 5
    allow_parallel: bool = False
    enable_reflection: bool = True

    def scale_for_simple(self) -> "ReasoningProfile":
        """Scale down for simple tasks."""
        return ReasoningProfile(
            intensity=ReasoningIntensity.MINIMAL,
            max_turns=10,
            checkpoint_interval=10,
            allow_parallel=False,
            enable_reflection=False
        )

    def scale_for_complex(self) -> "ReasoningProfile":
        """Scale up for complex tasks."""
        return ReasoningProfile(
            intensity=ReasoningIntensity.DEEP,
            max_turns=40,
            checkpoint_interval=3,
            allow_parallel=True,
            enable_reflection=True
        )


@dataclass
class ReasoningSignals:
    """Observable signals that influence reasoning intensity.

    These are external observations, not derived from context.
    Used to adjust the reasoning profile dynamically.

    Includes trend fields for predictive reasoning.
    """
    task_complexity: str = "MODERATE"  # SIMPLE/MODERATE/COMPLEX
    error_rate: float = 0.0           # 0.0-1.0, recent failure ratio
    success_streak: int = 0           # Consecutive successful phases
    context_budget_percent: float = 0.0  # Current context usage
    phase_duration_ms: float = 0.0    # How long current phase took
    tool_call_failure_ratio: float = 0.0  # Recent tool call failures
    # Trend indicators for predictive reasoning
    error_velocity_trend: TrendIndicator = field(
        default_factory=lambda: TrendIndicator(0.0, 0.0, 0.0)
    )
    plan_deviation_trend: TrendIndicator = field(
        default_factory=lambda: TrendIndicator(0.0, 0.0, 0.0)
    )

    def should_preemptively_escalate(self) -> bool:
        """Predictive check: both error velocity AND plan deviation worsening."""
        return (
            self.error_velocity_trend.trend == TrendDirection.WORSENING and
            self.plan_deviation_trend.trend == TrendDirection.WORSENING
        )


class DynamicReasoningEngine:
    """Adjust reasoning intensity based on observed signals.

    Signal-based adjustment rules:
    - HIGH complexity → DEEP
    - error_rate > 0.5 → increase intensity
    - success_streak > 5 → decrease intensity (confidence)
    - context_budget > 70% → DEGRADING mode, reduce turns
    - tool_call_failure_ratio > 0.3 → VERIFY mode

    Usage:
        engine = DynamicReasoningEngine()
        signals = ReasoningSignals(task_complexity="COMPLEX", error_rate=0.2)
        profile = engine.compute_profile(signals)
    """

    # Thresholds
    ERROR_RATE_HIGH = 0.5
    ERROR_RATE_MEDIUM = 0.3
    SUCCESS_STREAK_THRESHOLD = 5
    BUDGET_DEGRADING = 50.0
    BUDGET_POOR = 70.0
    TOOL_FAILURE_HIGH = 0.3

    def __init__(self):
        self._profile = ReasoningProfile()
        self._success_streak = 0
        self._error_rate = 0.0
        self._last_adjustment = datetime.now()

    @property
    def current_profile(self) -> ReasoningProfile:
        """Get the current reasoning profile."""
        return self._profile

    def compute_profile(self, signals: ReasoningSignals) -> ReasoningProfile:
        """Compute the optimal reasoning profile based on signals.

        This is the main entry point for profile adjustment.
        Call this at the start of each phase.

        Uses predictive reasoning: if signals indicate worsening trends,
        preemptively escalate reasoning intensity before problems compound.
        """
        profile = ReasoningProfile()  # Start with defaults

        # Predictive check: worsening trends require preemptive escalation
        if signals.should_preemptively_escalate():
            # Both error velocity AND plan deviation are worsening
            # Escalate immediately to prevent compounding problems
            profile.intensity = ReasoningIntensity.DEEP
            profile.max_turns = 40
            profile.checkpoint_interval = 3
            profile.enable_reflection = True
            profile.allow_parallel = True
            self._profile = profile
            return profile

        # Complexity-based scaling
        if signals.task_complexity == "SIMPLE":
            profile = profile.scale_for_simple()
        elif signals.task_complexity == "COMPLEX":
            profile = profile.scale_for_complex()

        # Error rate adjustment
        if signals.error_rate > self.ERROR_RATE_HIGH:
            # High errors → invest more in planning
            profile.intensity = ReasoningIntensity.DEEP
            profile.max_turns = min(40, profile.max_turns + 10)
            profile.enable_reflection = True
        elif signals.error_rate > self.ERROR_RATE_MEDIUM:
            profile.intensity = ReasoningIntensity.VERIFY

        # Success streak adjustment (decay toward minimal)
        if signals.success_streak > self.SUCCESS_STREAK_THRESHOLD:
            if profile.intensity != ReasoningIntensity.MINIMAL:
                # Gradually reduce intensity
                new_intensity = ReasoningIntensity(
                    max(ReasoningIntensity.MINIMAL.value,
                        profile.intensity.value - 1)
                )
                profile.intensity = new_intensity
                profile.max_turns = max(10, profile.max_turns - 5)

        # Context budget pressure
        if signals.context_budget_percent >= self.BUDGET_POOR:
            # Emergency mode — minimize overhead
            profile.intensity = ReasoningIntensity.MINIMAL
            profile.max_turns = min(10, profile.max_turns)
            profile.checkpoint_interval = max(2, profile.checkpoint_interval - 2)
        elif signals.context_budget_percent >= self.BUDGET_DEGRADING:
            # Degrading — reduce but maintain safety
            profile.checkpoint_interval = max(3, profile.checkpoint_interval - 1)

        # Tool failure ratio
        if signals.tool_call_failure_ratio > self.TOOL_FAILURE_HIGH:
            profile.intensity = ReasoningIntensity.VERIFY
            profile.enable_reflection = True

        self._profile = profile
        self._last_adjustment = datetime.now()
        return profile

    def on_phase_complete(
        self,
        phase: str,
        success: bool,
        turn_count: int
    ) -> ReasoningProfile:
        """Adjust profile based on phase outcome.

        Call this after each phase completes to update signals.
        """
        if success:
            self._success_streak += 1
            self._error_rate = max(0, self._error_rate - 0.1)
        else:
            self._success_streak = 0
            self._error_rate = min(1.0, self._error_rate + 0.2)

        signals = ReasoningSignals(
            error_rate=self._error_rate,
            success_streak=self._success_streak,
            context_budget_percent=getattr(self, '_context_budget', 0.0),
            phase_duration_ms=getattr(self, '_phase_duration_ms', 0.0)
        )

        return self.compute_profile(signals)

    def on_task_complete(self, complexity: str) -> ReasoningProfile:
        """Adjust based on completed task type."""
        signals = ReasoningSignals(
            task_complexity=complexity,
            error_rate=self._error_rate,
            success_streak=self._success_streak
        )
        return self.compute_profile(signals)

    def record_context_budget(self, percent: float) -> None:
        """Record context budget for next adjustment."""
        self._context_budget = percent

    def record_phase_duration(self, ms: float) -> None:
        """Record phase duration for next adjustment."""
        self._phase_duration_ms = ms

    def get_stats(self) -> dict:
        """Get reasoning engine statistics."""
        return {
            "current_intensity": self._profile.intensity.name,
            "max_turns": self._profile.max_turns,
            "success_streak": self._success_streak,
            "error_rate": round(self._error_rate, 2),
            "last_adjustment": self._last_adjustment.isoformat()
        }

    def reset(self) -> None:
        """Reset to default profile."""
        self._profile = ReasoningProfile()
        self._success_streak = 0
        self._error_rate = 0.0