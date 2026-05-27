"""FeedbackLoop Integration with DynamicReasoningEngine.

This module integrates FeedbackLoop events with reasoning intensity adjustment.
It bridges the gap between event production (FeedbackLoop) and
consumption (DynamicReasoningEngine).

Usage:
    # In executor initialization:
    feedback = IntegratedFeedbackLoop(
        webhook_url="...",
        reasoning_engine=AdaptiveReasoningEngine()
    )
    feedback.start()

    # Events now automatically adjust reasoning intensity
    feedback.on_task_failed("task-1", "timeout")

    # Get current reasoning profile (adjusted based on events)
    profile = feedback.get_current_reasoning_profile()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable
import threading
import queue
import json

from .feedback_loop import FeedbackLoop, FeedbackEventType, FeedbackEvent
from .reasoning_signal import ReasoningSignal, SignalComputer
from .dynamic_reasoning import ReasoningIntensity, ReasoningProfile, ReasoningSignals
from .adaptive_reasoning import AdaptiveReasoningEngine


@dataclass
class DeliveryStatus:
    """Tracks event delivery with retry support."""
    status: str = "pending"  # pending / confirmed / failed
    retry_count: int = 0
    max_retries: int = 3
    last_attempt: Optional[str] = None
    confirmed_at: Optional[str] = None


# External callback types for bidirectional feedback
ReasoningAdjustCallback = Callable[["IntegratedFeedbackLoop", ReasoningSignals], ReasoningSignals]
PhaseSelectCallback = Callable[["IntegratedFeedbackLoop", str], Optional[str]]


class IntegratedFeedbackLoop(FeedbackLoop):
    """FeedbackLoop with DynamicReasoningEngine integration.

    This extends the base FeedbackLoop to:
    1. Compute ReasoningSignals from events
    2. Notify reasoning engine of state changes
    3. Track delivery status with retries
    4. Provide current reasoning profile on demand
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        local_log_path: Optional[str] = None,
        enable_console: bool = True,
        reasoning_engine: Optional[AdaptiveReasoningEngine] = None,
    ):
        super().__init__(
            webhook_url=webhook_url,
            local_log_path=local_log_path,
            enable_console=enable_console
        )

        self._reasoning_engine = reasoning_engine or AdaptiveReasoningEngine()
        self._signal_computer = SignalComputer()
        self._delivery_status: dict[str, DeliveryStatus] = {}
        self._current_phase: str = ""
        self._reasoning_lock = threading.Lock()
        self._last_profile: Optional[ReasoningProfile] = None

        # Bidirectional feedback callbacks
        self._reasoning_adjust_callbacks: list[ReasoningAdjustCallback] = []
        self._phase_select_callbacks: list[PhaseSelectCallback] = []

        # Register internal handlers for signal computation
        self.on(FeedbackEventType.TASK_FAILED, self._on_task_failed_signal)
        self.on(FeedbackEventType.PHASE_COMPLETE, self._on_phase_complete_signal)
        self.on(FeedbackEventType.CONTEXT_DEGRADING, self._on_context_signal)
        self.on(FeedbackEventType.METRICS_UPDATE, self._on_metrics_signal)
        self.on(FeedbackEventType.PLAN_UPDATE, self._on_plan_signal)

    # ─── Bidirectional Feedback ─────────────────────────────────────────────────

    def on_reasoning_adjust(self, callback: ReasoningAdjustCallback) -> None:
        """Register a callback to adjust reasoning signals externally.

        This enables CI systems or other external monitors to influence
        reasoning intensity. For example, a slow CI could request VERIFY mode.

        Callback receives (IntegratedFeedbackLoop, ReasoningSignals) and
        returns modified ReasoningSignals.
        """
        self._reasoning_adjust_callbacks.append(callback)

    def on_phase_select(self, callback: PhaseSelectCallback) -> None:
        """Register a callback to override phase selection.

        This enables external systems to influence which phase runs next.
        For example, a test failure could request returning to PLAN.

        Callback receives (IntegratedFeedbackLoop, proposed_phase) and
        returns the phase to actually run (or None to cancel).
        """
        self._phase_select_callbacks.append(callback)

    def should_use_phase(self, proposed_phase: str) -> Optional[str]:
        """Check if proposed phase should run (after callbacks)."""
        for cb in self._phase_select_callbacks:
            result = cb(self, proposed_phase)
            if result is not None:
                return result
        return proposed_phase

    def _adjust_reasoning_with_callbacks(self, signals: ReasoningSignals) -> ReasoningSignals:
        """Apply external adjustments to reasoning signals."""
        result = signals
        for cb in self._reasoning_adjust_callbacks:
            result = cb(self, result)
        return result

    def _on_task_failed_signal(self, event: FeedbackEvent) -> None:
        """Record task failure for error velocity."""
        self._signal_computer.record_event("TASK_FAILED")
        self._adjust_reasoning()

    def _on_phase_complete_signal(self, event: FeedbackEvent) -> None:
        """Record phase completion for burn rate."""
        duration_ms = event.data.get("duration_ms", 0)
        if duration_ms:
            self._signal_computer.record_phase_duration(duration_ms)

        # Record context sample if present
        context_pct = event.data.get("context_percent", 0)
        if context_pct:
            self._signal_computer.record_context_sample(context_pct)

        # Update reasoning with phase outcome
        if self._reasoning_engine and self._current_phase:
            success = event.data.get("success", True)
            turn_count = event.data.get("turn_count", 0)

            self._reasoning_engine.record_phase_outcome(
                phase=self._current_phase,
                reasoning=self._last_profile.intensity if self._last_profile else ReasoningIntensity.MODERATE,
                success=success,
                context_used=context_pct,
                turn_count=turn_count,
            )

        self._adjust_reasoning()

    def _on_context_signal(self, event: FeedbackEvent) -> None:
        """Record context budget sample."""
        usage = event.data.get("usage_percent", 0)
        self._signal_computer.record_context_sample(usage)
        self._adjust_reasoning()

    def _on_metrics_signal(self, event: FeedbackEvent) -> None:
        """Record metrics for signal computation."""
        # Extract error rate if present
        error_rate = event.data.get("error_rate")
        if error_rate is not None:
            self._signal_computer.record_event(f"ERROR_RATE:{error_rate}")

    def _on_plan_signal(self, event: FeedbackEvent) -> None:
        """Record plan vs actual for deviation."""
        planned = event.data.get("planned", "")
        actual = event.data.get("actual", "")
        if planned and actual:
            self._signal_computer.record_plan_actual(planned, actual)
        self._adjust_reasoning()

    def _adjust_reasoning(self) -> None:
        """Adjust reasoning profile based on signals including trends and external callbacks."""
        with self._reasoning_lock:
            signal = self._signal_computer.compute()

            # Map FeedbackLoop signals to ReasoningSignals including trends
            from .dynamic_reasoning import ReasoningSignals

            reasoning_signals = ReasoningSignals(
                task_complexity=getattr(self._reasoning_engine, '_task_complexity', 'MODERATE'),
                error_rate=min(1.0, signal.error_velocity / 10.0),  # Normalize
                success_streak=getattr(self._reasoning_engine, '_success_streak', 0),
                context_budget_percent=signal.context_burn_rate * 10,  # Estimate
                error_velocity_trend=signal.error_velocity_trend,
                plan_deviation_trend=signal.plan_deviation_trend,
            )

            # Apply external bidirectional feedback adjustments
            reasoning_signals = self._adjust_reasoning_with_callbacks(reasoning_signals)

            self._last_profile = self._reasoning_engine.compute_profile(
                reasoning_signals,
                phase=self._current_phase
            )

    # ─── Delivery Status Tracking ──────────────────────────────────────────────

    def set_current_phase(self, phase: str) -> None:
        """Set the current phase for reasoning adjustment."""
        self._current_phase = phase

    def get_current_reasoning_profile(self) -> ReasoningProfile:
        """Get the current reasoning profile (adjusted by events)."""
        with self._reasoning_lock:
            if self._last_profile is None:
                self._last_profile = self._reasoning_engine.current_profile
            return self._last_profile

    def get_delivery_status(self, event_id: str) -> Optional[DeliveryStatus]:
        """Get delivery status for a specific event."""
        return self._delivery_status.get(event_id)

    def confirm_delivery(self, event_id: str) -> None:
        """Confirm successful event delivery (for retry tracking)."""
        if event_id in self._delivery_status:
            self._delivery_status[event_id].status = "confirmed"
            self._delivery_status[event_id].confirmed_at = datetime.now().isoformat()

    def _notify_delivery_result(self, event: FeedbackEvent, success: bool) -> None:
        """Track webhook delivery result for retry management."""
        event_id = f"{event.type.name}:{event.timestamp}"
        if event_id in self._delivery_status:
            status = self._delivery_status[event_id]
            status.status = "confirmed" if success else "pending_retry"
            if not success:
                # Will be retried by parent's retry logic
                pass
            else:
                self.confirm_delivery(event_id)

    # ─── Override event triggers with reasoning updates ─────────────────────────

    def on_task_failed(self, task_id: str, error: str) -> None:
        """Override to also trigger reasoning adjustment."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.TASK_FAILED,
            data={"task_id": task_id, "error": error, "timestamp": datetime.now().isoformat()}
        ))
        # Immediate signal update
        self._signal_computer.record_event("TASK_FAILED")
        self._adjust_reasoning()

    def on_task_completed(self, task_id: str, result: dict) -> None:
        """Override to also trigger reasoning adjustment."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.TASK_COMPLETED,
            data={"task_id": task_id, "result": result, "timestamp": datetime.now().isoformat()}
        ))

    def on_phase_complete(
        self,
        phase: str,
        turn_count: int,
        success: bool
    ) -> None:
        """Override to also update reasoning engine."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.PHASE_COMPLETE,
            data={
                "phase": phase,
                "turn_count": turn_count,
                "success": success,
                "timestamp": datetime.now().isoformat()
            }
        ))

    def on_plan_update(self, planned: str, actual: str) -> None:
        """Notify of plan vs actual for deviation tracking."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.PLAN_UPDATE,
            data={"planned": planned, "actual": actual}
        ))
        self._signal_computer.record_plan_actual(planned, actual)
        self._adjust_reasoning()

    def get_learning_stats(self) -> dict:
        """Get reasoning engine learning statistics."""
        if hasattr(self._reasoning_engine, 'get_learning_stats'):
            return self._reasoning_engine.get_learning_stats()
        return {}

    def reset_reasoning(self) -> None:
        """Reset reasoning engine state."""
        if hasattr(self._reasoning_engine, 'reset'):
            self._reasoning_engine.reset()
        self._signal_computer.clear()
        self._last_profile = None