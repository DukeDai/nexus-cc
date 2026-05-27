"""DecisionContextBuilder — Guarantees clean decision context for orchestrator.

Core invariant: The orchestrator's decision context is ALWAYS trace-free.
No error details, no full tool call sequences, no raw outputs — only:
1. Error metrics and recovery hints
2. Phase completion summaries (compressed)
3. Minimal state snapshot from checkpoint
4. Reasoning signal if available

This module is the SOLE entry point for building context used in:
- Transition guard evaluation
- State machine decisions
- Retry/abort determinations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .error_isolation import ShadowErrorTracker, ErrorDecisionContext
    from .phase_isolation import IsolatedContextManager
    from .dynamic_reasoning import ReasoningSignal
    from .task_graph import TaskGraph
    from ..context.checkpoint import CheckpointData


class DecisionQuality(Enum):
    """How trustworthy is this decision context."""
    HIGH = auto()      # All components available, fresh data
    MEDIUM = auto()    # Some components stale or missing
    LOW = auto()       # Minimal context, use with caution


@dataclass(frozen=True)
class DecisionContext:
    """Clean context for orchestrator decision-making.

    This is the ONLY thing the orchestrator should use for decisions.
    Never contains: error traces, tool call details, raw outputs.

    Attributes:
        quality: How trustworthy this context is
        phase_summary: Compressed summary of current/recent phases
        error_context: Error metrics without traces
        checkpoint_state: Minimal state from checkpoint
        reasoning_signal: Current reasoning intensity recommendation
        tasks_ready: Number of tasks ready to claim
        timestamp: When this context was built
        is_fresh: False if this is stale (>30s old)
    """
    quality: DecisionQuality = DecisionQuality.HIGH
    phase_summary: str = ""
    error_context: 'ErrorDecisionContext' = field(
        default_factory=lambda: ErrorDecisionContext()  # type: ignore
    )
    checkpoint_state: Optional['CheckpointData'] = None
    reasoning_signal: Optional['ReasoningSignal'] = None  # type: ignore
    tasks_ready: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    is_fresh: bool = True

    @property
    def is_stale(self) -> bool:
        """Check if this context is too old to trust."""
        return not self.is_fresh

    @property
    def should_retry(self) -> bool:
        """Short-cut for retry decision."""
        return self.error_context.should_retry

    @property
    def is_stuck(self) -> bool:
        """Check if we're in a failure loop."""
        return self.error_context.is_stuck


class DecisionContextBuilder:
    """Builds guaranteed-clean decision contexts.

    Usage:
        builder = DecisionContextBuilder(
            error_tracker=shadow_tracker,
            phase_manager=isolated_manager,
            task_graph=task_graph,
        )
        ctx = builder.build(current_phase="ACT")
        # ctx is guaranteed trace-free
        is_valid = ctx.should_retry  # Safe to use in guards
    """

    STALE_THRESHOLD_SECONDS = 30.0

    def __init__(
        self,
        error_tracker: 'ShadowErrorTracker',
        phase_manager: 'IsolatedContextManager',
        task_graph: Optional['TaskGraph'] = None,
        checkpoint_data: Optional['CheckpointData'] = None,
        reasoning_signal: Optional['ReasoningSignal'] = None,
    ):
        self.error_tracker = error_tracker
        self.phase_manager = phase_manager
        self.task_graph = task_graph
        self.checkpoint_data = checkpoint_data
        self.reasoning_signal = reasoning_signal
        self._last_build_time: Optional[datetime] = None

    def build(self, current_phase: str) -> DecisionContext:
        """Build a clean decision context.

        Args:
            current_phase: The RalphState phase we're in now

        Returns:
            DecisionContext with guaranteed no error traces
        """
        self._last_build_time = datetime.now()

        # 1. Error context — from ShadowErrorTracker (never contains traces)
        error_ctx = self.error_tracker.get_decision_context()

        # 2. Phase summary — from IsolatedContextManager (compressed)
        phase_summary = self._build_phase_summary(current_phase)

        # 3. Tasks ready — from TaskGraph if available
        tasks_ready = self._get_tasks_ready()

        # 4. Assess quality
        quality = self._assess_quality()

        return DecisionContext(
            quality=quality,
            phase_summary=phase_summary,
            error_context=error_ctx,
            checkpoint_state=self.checkpoint_data,
            reasoning_signal=self.reasoning_signal,
            tasks_ready=tasks_ready,
            timestamp=self._last_build_time.isoformat(),
            is_fresh=True,
        )

    def build_minimal(self) -> DecisionContext:
        """Build a minimal context when full data unavailable.

        Use this when CheckpointManager or PhaseManager are unavailable
        (e.g., during early initialization or after catastrophic failure).
        """
        return DecisionContext(
            quality=DecisionQuality.LOW,
            error_context=self.error_tracker.get_decision_context(),
            timestamp=datetime.now().isoformat(),
            is_fresh=True,
        )

    def _build_phase_summary(self, current_phase: str) -> str:
        """Build phase summary without any raw data."""
        parts = []

        # Current phase status
        current = self.phase_manager.get_current_phase()
        if current:
            if current.compressed:
                parts.append(f"[{current.phase}] COMPRESSED: {current.summary}")
            else:
                msg_count = len(current.messages)
                tool_count = len(current.tool_results)
                parts.append(f"[{current.phase}] active: {msg_count} msgs, {tool_count} tools")

        # Recent phase summaries (compressed, safe to show)
        recent = self.phase_manager.summary_history[-3:] if self.phase_manager.summary_history else []
        if recent:
            parts.append("=== Recent Phases ===")
            parts.extend(recent)

        # Context budget
        budget = self.phase_manager.estimate_budget()
        parts.append(f"Context budget: {budget:.1f}%")

        return "\n".join(parts) if parts else "No phase context available."

    def _get_tasks_ready(self) -> int:
        """Get ready task count from task graph."""
        if self.task_graph is None:
            return 0
        return len(self.task_graph.get_ready_tasks())

    def _assess_quality(self) -> DecisionQuality:
        """Assess how complete this decision context is."""
        score = 0

        if self.error_tracker is not None:
            score += 1
        if self.phase_manager is not None:
            score += 1
        if self.task_graph is not None:
            score += 1
        if self.checkpoint_data is not None:
            score += 1

        if score >= 4:
            return DecisionQuality.HIGH
        elif score >= 2:
            return DecisionQuality.MEDIUM
        else:
            return DecisionQuality.LOW

    def is_context_fresh(self) -> bool:
        """Check if last built context is still fresh."""
        if self._last_build_time is None:
            return False

        elapsed = (datetime.now() - self._last_build_time).total_seconds()
        return elapsed < self.STALE_THRESHOLD_SECONDS