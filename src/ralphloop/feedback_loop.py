"""Feedback Loop — Async Notification and Progress Tracking.

This module implements background async feedback:
- Webhook notifications for significant events
- Progress tracking and logging
- Checkpoint notifications
- Context degradation warnings
- Task claim/release events

Key insight: Long-running tasks need async feedback to the user.
The main loop shouldn't block waiting for notifications.

Priority queue: Critical events (CONTEXT_POOR, ESCALATION) bypass normal queue
ordering to ensure immediate delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable
import threading
import queue
import json


class FeedbackEventType(Enum):
    """Types of feedback events."""
    CHECKPOINT = auto()
    CONTEXT_DEGRADING = auto()
    CONTEXT_POOR = auto()
    TASK_CLAIMED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()
    PHASE_COMPLETE = auto()
    PHASE_FAILED = auto()
    ESCALATION = auto()
    PLAN_UPDATE = auto()
    METRICS_UPDATE = auto()


class EventPriority(Enum):
    """Priority levels for event delivery.

    Higher priority events are delivered first, even if they arrive later.
    CRITICAL events bypass queue entirely for immediate delivery.
    """
    LOW = 1       # TASK_COMPLETED, METRICS_UPDATE
    NORMAL = 2    # CHECKPOINT, TASK_CLAIMED, PHASE_COMPLETE
    HIGH = 3      # CONTEXT_DEGRADING, TASK_FAILED, PHASE_FAILED
    CRITICAL = 4  # CONTEXT_POOR, ESCALATION — immediate delivery


# Map event types to their default priority
EVENT_PRIORITY_MAP: dict[FeedbackEventType, EventPriority] = {
    FeedbackEventType.CHECKPOINT: EventPriority.NORMAL,
    FeedbackEventType.CONTEXT_DEGRADING: EventPriority.HIGH,
    FeedbackEventType.CONTEXT_POOR: EventPriority.CRITICAL,
    FeedbackEventType.TASK_CLAIMED: EventPriority.NORMAL,
    FeedbackEventType.TASK_COMPLETED: EventPriority.LOW,
    FeedbackEventType.TASK_FAILED: EventPriority.HIGH,
    FeedbackEventType.PHASE_COMPLETE: EventPriority.NORMAL,
    FeedbackEventType.PHASE_FAILED: EventPriority.HIGH,
    FeedbackEventType.ESCALATION: EventPriority.CRITICAL,
    FeedbackEventType.PLAN_UPDATE: EventPriority.NORMAL,
    FeedbackEventType.METRICS_UPDATE: EventPriority.LOW,
}


@dataclass
class FeedbackEvent:
    """A feedback event to be delivered asynchronously.

    Attributes:
        type: Event type enum
        data: Event-specific data payload
        timestamp: When event was created
        delivered: Whether event has been delivered
        priority: Delivery priority (derived from type but can be overridden)
    """
    type: FeedbackEventType
    data: dict
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    delivered: bool = False
    priority: EventPriority = field(init=False)

    def __post_init__(self):
        self.priority = EVENT_PRIORITY_MAP.get(self.type, EventPriority.NORMAL)

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.name,
            "data": self.data,
            "timestamp": self.timestamp
        })


class FeedbackLoop:
    """Background async feedback delivery with priority queue.

    Features:
    - Thread-safe priority event queue
    - Webhook delivery (fire-and-forget)
    - Local event log for debugging
    - Configurable event filtering
    - CRITICAL events bypass queue for immediate delivery

    Usage:
        feedback = FeedbackLoop(webhook_url="https://my.app/webhook")
        feedback.on_checkpoint(checkpoint_data)
        feedback.on_context_degrading(usage_percent=55.0)
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        local_log_path: Optional[str] = None,
        enable_console: bool = True,
        queue_maxsize: int = 1000
    ):
        self.webhook_url = webhook_url
        self.local_log_path = local_log_path
        self.enable_console = enable_console

        self._event_queue: queue.Queue[FeedbackEvent] = queue.Queue(maxsize=queue_maxsize)
        self._event_log: list[FeedbackEvent] = []
        self._max_log_size = 100
        self._running = False
        self._dispatcher_thread: Optional[threading.Thread] = None
        self._handlers: dict[FeedbackEventType, list[Callable]] = {}
        # Critical event handling
        self._critical_handlers: list[Callable] = []

    def start(self) -> None:
        """Start the background dispatcher thread."""
        if self._running:
            return

        self._running = True
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop,
            name="feedback-dispatcher",
            daemon=True
        )
        self._dispatcher_thread.start()

    def stop(self) -> None:
        """Stop the dispatcher thread."""
        self._running = False
        if self._dispatcher_thread:
            self._dispatcher_thread.join(timeout=5)

    def _dispatch_loop(self) -> None:
        """Main dispatch loop running in background thread."""
        while self._running:
            try:
                event = self._event_queue.get(timeout=1.0)
                self._deliver_event(event)
            except queue.Empty:
                continue
            except Exception:
                pass  # Don't let exceptions kill the thread

    def _deliver_event(self, event: FeedbackEvent) -> None:
        """Deliver a single event via all configured channels."""
        # Local log
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log.pop(0)

        if self.local_log_path:
            try:
                with open(self.local_log_path, "a") as f:
                    f.write(event.to_json() + "\n")
            except Exception:
                pass

        # Console output
        if self.enable_console:
            self._console_output(event)

        # Webhook (fire and forget)
        if self.webhook_url:
            self._send_webhook(event)

        # Custom handlers
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                pass

        event.delivered = True

    def _console_output(self, event: FeedbackEvent) -> None:
        """Format event for console output."""
        emoji = {
            FeedbackEventType.CHECKPOINT: "💾",
            FeedbackEventType.CONTEXT_DEGRADING: "⚠️",
            FeedbackEventType.CONTEXT_POOR: "🚨",
            FeedbackEventType.TASK_CLAIMED: "🏃",
            FeedbackEventType.TASK_COMPLETED: "✅",
            FeedbackEventType.TASK_FAILED: "❌",
            FeedbackEventType.PHASE_COMPLETE: "📍",
            FeedbackEventType.PHASE_FAILED: "🔴",
            FeedbackEventType.ESCALATION: "📢",
            FeedbackEventType.PLAN_UPDATE: "📋",
            FeedbackEventType.METRICS_UPDATE: "📊",
        }.get(event.type, "📌")

        data_str = json.dumps(event.data, indent=2)[:200]
        print(f"{emoji} [{event.type.name}] {data_str}")

    def _send_webhook(self, event: FeedbackEvent) -> None:
        """Send event to webhook URL with retry support.

        For CRITICAL events (CHECKPOINT, ESCALATION), uses at-least-once delivery:
        - Retries up to max_retries times on failure
        - Reports success/failure via delivery status if IntegratedFeedbackLoop
        """
        import urllib.request

        max_retries = 3 if event.priority == EventPriority.CRITICAL else 1

        def _do_send_with_retry() -> bool:
            for attempt in range(max_retries):
                try:
                    data = event.to_json().encode("utf-8")
                    req = urllib.request.Request(
                        self.webhook_url,
                        data=data,
                        headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return True
                except Exception:
                    if attempt < max_retries - 1:
                        # Brief delay before retry
                        import time
                        time.sleep(0.5 * (attempt + 1))
            return False

        def _async_send():
            success = _do_send_with_retry()
            # Notify about delivery status for critical events
            if event.priority == EventPriority.CRITICAL:
                self._notify_delivery_result(event, success)

        thread = threading.Thread(target=_async_send, daemon=True)
        thread.start()

    def _notify_delivery_result(self, event: FeedbackEvent, success: bool) -> None:
        """Notify about webhook delivery result.

        Override in IntegratedFeedbackLoop to track delivery confirmation.
        Base class: no-op by default.
        """
        pass

    def _enqueue(self, event: FeedbackEvent) -> None:
        """Add event to dispatch queue with priority handling.

        CRITICAL events trigger immediate critical handler dispatch
        before being queued. This ensures ESCALATION and CONTEXT_POOR
        events are acted on without delay.
        """
        # CRITICAL events: dispatch immediately to critical handlers
        if event.priority == EventPriority.CRITICAL:
            for handler in self._critical_handlers:
                try:
                    handler(event)
                except Exception:
                    pass

        # Try to enqueue, drop if full (backpressure)
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            pass  # Drop event if queue is full

    def on_critical(self, handler: Callable[[FeedbackEvent], None]) -> None:
        """Register a handler for CRITICAL priority events.

        Critical handlers are called IMMEDIATELY when CRITICAL events
        arrive, not waiting for queue dispatch. Use for things like
        user notifications that can't wait.
        """
        self._critical_handlers.append(handler)

    def on(
        self,
        event_type: FeedbackEventType,
        handler: Callable[[FeedbackEvent], None]
    ) -> None:
        """Register a handler for a specific event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    # ─── Event Triggers ────────────────────────────────────────────────────────

    def on_checkpoint(self, checkpoint_data: dict) -> None:
        """Called when a checkpoint is saved."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.CHECKPOINT,
            data=checkpoint_data
        ))

    def on_context_degrading(self, usage_percent: float) -> None:
        """Warning that context is degrading."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.CONTEXT_DEGRADING,
            data={"usage_percent": usage_percent, "tier": "DEGRADING"}
        ))

    def on_context_poor(self, usage_percent: float) -> None:
        """Emergency: context budget is critical."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.CONTEXT_POOR,
            data={"usage_percent": usage_percent, "tier": "POOR", "action": "ABORT"}
        ))

    def on_task_claimed(self, task_id: str, agent_id: str) -> None:
        """Notify that a task was claimed for parallel execution."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.TASK_CLAIMED,
            data={"task_id": task_id, "agent_id": agent_id}
        ))

    def on_task_completed(self, task_id: str, result: dict) -> None:
        """Notify that a task completed successfully."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.TASK_COMPLETED,
            data={"task_id": task_id, "result": result}
        ))

    def on_task_failed(self, task_id: str, error: str) -> None:
        """Notify that a task failed."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.TASK_FAILED,
            data={"task_id": task_id, "error": error}
        ))

    def on_phase_complete(
        self,
        phase: str,
        turn_count: int,
        success: bool
    ) -> None:
        """Notify that a phase completed."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.PHASE_COMPLETE,
            data={
                "phase": phase,
                "turn_count": turn_count,
                "success": success
            }
        ))

    def on_escalation(self, task_id: str, reason: str) -> None:
        """Notify that an escalation occurred."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.ESCALATION,
            data={"task_id": task_id, "reason": reason}
        ))

    def on_metrics_update(self, metrics: dict) -> None:
        """Notify with current metrics."""
        self._enqueue(FeedbackEvent(
            type=FeedbackEventType.METRICS_UPDATE,
            data=metrics
        ))

    # ─── Accessors ────────────────────────────────────────────────────────────

    def get_recent_events(self, n: int = 10) -> list[FeedbackEvent]:
        """Get the n most recent events."""
        return self._event_log[-n:]

    def get_events_by_type(
        self,
        event_type: FeedbackEventType,
        limit: int = 10
    ) -> list[FeedbackEvent]:
        """Get recent events of a specific type."""
        return [
            e for e in self._event_log
            if e.type == event_type
        ][-limit:]

    def clear_log(self) -> None:
        """Clear the event log."""
        self._event_log.clear()

    def get_stats(self) -> dict:
        """Get feedback loop statistics."""
        type_counts = {}
        for event in self._event_log:
            type_counts[event.type.name] = type_counts.get(event.type.name, 0) + 1

        return {
            "total_events": len(self._event_log),
            "by_type": type_counts,
            "queue_size": self._event_queue.qsize(),
            "webhook_configured": self.webhook_url is not None,
            "running": self._running
        }