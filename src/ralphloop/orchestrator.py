"""RalphLoop Orchestrator — Main State Machine Engine.

The RalphLoop is a closed-loop self-correction orchestration engine
implementing: PLAN → ACT → VERIFY → REFLECT cycle with explicit state
transitions, retry logic, escalation, and checkpoint capability.

Key Features:
    - Explicit state transitions (no implicit state)
    - Max 3 retries per task before escalation
    - Context budget monitoring with 4-tier degradation
    - Checkpoint capability for recovery
    - Clean separation between state machine logic and agent execution
"""

from __future__ import annotations

import time
import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional, Any

from .states import RalphState
from .transitions import (
    Transition,
    TransitionContext,
    TransitionTrigger,
    get_valid_transitions,
    get_abort_transition,
)


class ContextTier(Enum):
    """Four-tier context budget model per SPEC.md.

    | Tier        | Usage    | Orchestrator Action                      |
    |-------------|----------|------------------------------------------|
    | PEAK        | 0-30%    | Full operations, spawn parallel agents   |
    | GOOD        | 30-50%   | Normal operations, prefer frontmatter    |
    | DEGRADING   | 50-70%   | Economize, frontmatter-only, warn user   |
    | POOR        | 70%+     | Emergency, checkpoint and stop          |
    """
    PEAK = auto()
    GOOD = auto()
    DEGRADING = auto()
    POOR = auto()

    @classmethod
    def from_usage(cls, usage_percent: float) -> ContextTier:
        if usage_percent < 30:
            return cls.PEAK
        elif usage_percent < 50:
            return cls.GOOD
        elif usage_percent < 70:
            return cls.DEGRADING
        else:
            return cls.POOR

    def should_warn(self) -> bool:
        return self == ContextTier.DEGRADING

    def should_abort(self) -> bool:
        return self == ContextTier.POOR


class EscalationOption(Enum):
    """User options when RalphLoop exhausts retries."""
    FORCE_MERGE = "force-merge"
    REWRITE = "rewrite"
    ABANDON = "abandon"
    DECOMPOSE = "decompose"


@dataclass
class Checkpoint:
    """Checkpoint data for recovery.

    Attributes:
        timestamp: ISO timestamp when checkpoint was created.
        state: RalphState at time of checkpoint.
        task_index: Index of current task in queue.
        retry_count: Number of retries on current task.
        context_usage: Context usage percentage at checkpoint.
        task_queue: Remaining task queue (serialized).
        error_log: Recent error messages for debugging.
    """
    timestamp: str
    state: RalphState
    task_index: int
    retry_count: int
    context_usage: float
    task_queue: list[dict[str, Any]]
    error_log: list[str]

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "state": self.state.name,
            "task_index": self.task_index,
            "retry_count": self.retry_count,
            "context_usage": self.context_usage,
            "task_queue": self.task_queue,
            "error_log": self.error_log,
        }, indent=2)

    @classmethod
    def from_json(cls, data: str) -> Checkpoint:
        d = json.loads(data)
        return cls(
            timestamp=d["timestamp"],
            state=RalphState[d["state"]],
            task_index=d["task_index"],
            retry_count=d["retry_count"],
            context_usage=d["context_usage"],
            task_queue=d["task_queue"],
            error_log=d["error_log"],
        )


@dataclass
class RalphLoopMetrics:
    """Runtime metrics for RalphLoop execution."""
    total_iterations: int = 0
    total_retries: int = 0
    total_escalations: int = 0
    context_tier_changes: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    states_visited: list[RalphState] = field(default_factory=list)

    def record_state(self, state: RalphState) -> None:
        self.states_visited.append(state)
        self.total_iterations += 1

    def duration_seconds(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


class RalphLoop:
    """RalphLoop state machine orchestrator.

    Implements the PLAN → ACT → VERIFY → REFLECT loop with:
        - Explicit state transitions via transition table
        - Max 3 retries per task before escalation
        - Context budget 4-tier monitoring
        - Checkpoint save/load for recovery

    Usage:
        orchestrator = RalphLoop(
            task_queue=[...],
            context_monitor=context_monitor,
            checkpoint_dir=Path(".nexus/checkpoints")
        )
        result = orchestrator.run()

    Attributes:
        task_queue: List of tasks to process.
        context_monitor: Callable returning context usage (0-100).
        checkpoint_dir: Directory for checkpoint files.
        on_state_change: Optional callback(old_state, new_state).
        on_escalation: Optional callback(escalation_context) -> EscalationOption.
        on_warning: Optional callback(tier, message).
    """

    MAX_RETRIES: int = 3
    CHECKPOINT_INTERVAL: int = 5  # checkpoints every N iterations

    def __init__(
        self,
        task_queue: list[dict[str, Any]],
        context_monitor: Callable[[], float],
        checkpoint_dir: Optional[Path] = None,
        on_state_change: Optional[Callable[[RalphState, RalphState], None]] = None,
        on_escalation: Optional[Callable[[dict[str, Any]], EscalationOption]] = None,
        on_warning: Optional[Callable[[ContextTier, str], None]] = None,
        agent_executor: Optional[Callable[..., dict[str, Any]]] = None,
    ):
        """Initialize RalphLoop orchestrator.

        Args:
            task_queue: List of task dicts to process.
            context_monitor: Callable returning context usage 0-100.
            checkpoint_dir: Path for checkpoint files. None to disable.
            on_state_change: Callback(old_state, new_state).
            on_escalation: Callback returning user's escalation choice.
            on_warning: Callback(tier, message) for tier warnings.
            agent_executor: Callable(task, phase) -> result dict.
        """
        self.task_queue = task_queue
        self.context_monitor = context_monitor
        self.checkpoint_dir = checkpoint_dir
        self.on_state_change = on_state_change
        self.on_escalation = on_escalation
        self.on_warning = on_warning
        self.agent_executor = agent_executor or self._default_agent_executor

        self.state: RalphState = RalphState.PLAN
        self.task_index: int = 0
        self.retry_count: int = 0
        self.error_log: list[str] = []
        self.metrics = RalphLoopMetrics()
        self._running: bool = False
        self._checkpoint_count: int = 0

        # Validate checkpoint dir exists
        if self.checkpoint_dir:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _default_agent_executor(
        self, task: dict[str, Any], phase: RalphState
    ) -> dict[str, Any]:
        """Default agent executor stub.

        In production, this dispatches to actual agents.
        Returns result dict with keys: success, error, result.
        """
        return {
            "success": True,
            "error": None,
            "result": f"[{phase.name}] Processed: {task.get('description', 'unknown')}"
        }

    @property
    def context_tier(self) -> ContextTier:
        """Current context tier based on usage."""
        return ContextTier.from_usage(self.context_monitor())

    @property
    def context_usage(self) -> float:
        """Current context usage percentage."""
        return self.context_monitor()

    def _build_context(self) -> TransitionContext:
        """Build TransitionContext for guard evaluation."""
        return TransitionContext(
            retry_count=self.retry_count,
            context_usage_percent=self.context_usage,
            tasks_remaining=len(self.task_queue) - self.task_index,
            current_error=self.error_log[-1] if self.error_log else None,
            escalation_options_selected=None,
        )

    def _transition(
        self, trigger: TransitionTrigger, context: TransitionContext
    ) -> Optional[Transition]:
        """Attempt to transition via trigger.

        Returns Transition if valid, None if no valid transition.
        Raises Abort if context budget POOR.
        """
        # Check for abort condition first (context budget)
        if context.context_usage_percent >= 70.0:
            abort_t = get_abort_transition(self.state)
            if abort_t:
                self.state = RalphState.ABORT
                return abort_t

        valid = get_valid_transitions(self.state, trigger, context)
        if valid:
            t = valid[0]  # Take first matching transition
            old_state = self.state
            self.state = t.to_state
            if self.on_state_change and old_state != t.to_state:
                self.on_state_change(old_state, t.to_state)
            return t
        return None

    def _checkpoint(self) -> Optional[Path]:
        """Save checkpoint to disk.

        Returns path to checkpoint file, or None if disabled.
        """
        if not self.checkpoint_dir:
            return None

        checkpoint = Checkpoint(
            timestamp=datetime.now().isoformat(),
            state=self.state,
            task_index=self.task_index,
            retry_count=self.retry_count,
            context_usage=self.context_usage,
            task_queue=self.task_queue[self.task_index:],
            error_log=self.error_log[-10:],  # Last 10 errors
        )

        path = self.checkpoint_dir / f"checkpoint_{self._checkpoint_count}.json"
        path.write_text(checkpoint.to_json())
        self._checkpoint_count += 1
        return path

    def load_checkpoint(self, path: Path) -> bool:
        """Restore state from checkpoint file.

        Args:
            path: Path to checkpoint JSON file.

        Returns:
            True if loaded successfully, False otherwise.
        """
        try:
            checkpoint = Checkpoint.from_json(path.read_text())
            self.state = checkpoint.state
            self.task_index = checkpoint.task_index
            self.retry_count = checkpoint.retry_count
            self.error_log = checkpoint.error_log
            self.task_queue = self.task_queue[:checkpoint.task_index] + checkpoint.task_queue
            return True
        except Exception:
            return False

    def _handle_exception(self, exc: Exception) -> None:
        """Handle exception during state execution.

        Records error and increments retry count.
        """
        tb = traceback.format_exc()
        error_msg = f"[{datetime.now().isoformat()}] {type(exc).__name__}: {exc}\n{tb}"
        self.error_log.append(error_msg)
        self.retry_count += 1
        self.metrics.total_retries += 1

    def _check_context_tier_warnings(self) -> None:
        """Emit warnings if context tier changes to DEGRADING."""
        tier = self.context_tier
        if tier.should_warn() and self.on_warning:
            self.on_warning(
                tier,
                f"Context budget {self.context_usage:.1f}% — "
                "entering DEGRADING mode. Frontmatter-only reads, minimal inlining."
            )

    def _execute_state(self) -> TransitionTrigger:
        """Execute current state and return trigger for next transition.

        Each state method returns a TransitionTrigger indicating the
        outcome of the state execution.
        """
        task = self.task_queue[self.task_index] if self.task_index < len(self.task_queue) else {}

        if self.state == RalphState.PLAN:
            result = self.agent_executor(task, RalphState.PLAN)
            if result.get("success"):
                self.retry_count = 0  # Reset on successful plan
                return TransitionTrigger.SPEC_VALID
            else:
                self._handle_exception(Exception(result.get("error", "Plan failed")))
                return TransitionTrigger.VERIFICATION_FAILED

        elif self.state == RalphState.ACT:
            result = self.agent_executor(task, RalphState.ACT)
            if result.get("success"):
                return TransitionTrigger.IMPLEMENTATION_COMPLETE
            else:
                self._handle_exception(Exception(result.get("error", "Act failed")))
                return TransitionTrigger.VERIFICATION_FAILED

        elif self.state == RalphState.VERIFY:
            result = self.agent_executor(task, RalphState.VERIFY)
            if result.get("success"):
                return TransitionTrigger.VERIFICATION_PASSED
            else:
                self._handle_exception(Exception(result.get("error", "Verify failed")))
                if self.retry_count >= self.MAX_RETRIES:
                    return TransitionTrigger.MAX_RETRIES_EXCEEDED
                return TransitionTrigger.VERIFICATION_FAILED

        elif self.state == RalphState.REFLECT:
            result = self.agent_executor(task, RalphState.REFLECT)
            self.retry_count = 0  # Reset on successful reflection
            if self.task_index < len(self.task_queue) - 1:
                self.task_index += 1
                return TransitionTrigger.NEXT_TASK_AVAILABLE
            else:
                # Completed the last task - mark as done
                self.task_index += 1
                return TransitionTrigger.ALL_TASKS_COMPLETE

        elif self.state == RalphState.ESCALATE:
            if self.on_escalation:
                option = self.on_escalation({
                    "task": task,
                    "retry_count": self.retry_count,
                    "error_log": self.error_log[-5:],
                })
                self.metrics.total_escalations += 1
                # Return based on user's choice
                if option == EscalationOption.ABANDON:
                    # Skip current task, go to commit
                    return TransitionTrigger.USER_ESCALATION_RESPONSE
                else:
                    # Rewrite or decompose - restart from plan
                    self.task_index += 1
                    self.retry_count = 0
                    return TransitionTrigger.USER_ESCALATION_RESPONSE
            return TransitionTrigger.MAX_RETRIES_EXCEEDED

        elif self.state == RalphState.ABORT:
            return TransitionTrigger.CONTEXT_BUDGET_POOR

        elif self.state == RalphState.COMMIT:
            return TransitionTrigger.ALL_TASKS_COMPLETE

        return TransitionTrigger.VERIFICATION_FAILED

    def run(self) -> dict[str, Any]:
        """Execute the RalphLoop state machine.

        Returns:
            dict with keys: success, final_state, metrics, checkpoint_path.
        """
        self._running = True
        self.metrics.start_time = datetime.now()
        self.metrics.record_state(self.state)

        try:
            while self._running:
                # Check context tier warnings
                self._check_context_tier_warnings()

                # Checkpoint periodically
                if self._checkpoint_count > 0 and self.metrics.total_iterations % self.CHECKPOINT_INTERVAL == 0:
                    self._checkpoint()

                # Execute current state
                trigger = self._execute_state()
                context = self._build_context()

                # Attempt transition
                t = self._transition(trigger, context)
                if t is None:
                    # No valid transition - should not happen normally
                    # Log and attempt graceful exit
                    self.error_log.append(
                        f"No valid transition: state={self.state.name}, trigger={trigger.name}"
                    )
                    break

                self.metrics.record_state(self.state)

                # Check terminal states
                if self.state == RalphState.COMMIT:
                    self._running = False
                elif self.state == RalphState.ABORT:
                    self._checkpoint()  # Final checkpoint before abort
                    self._running = False
                elif self.state == RalphState.ESCALATE and t.to_state == RalphState.COMMIT:
                    # Abandon selected
                    self._running = False

        finally:
            self.metrics.end_time = datetime.now()
            self._running = False

        return {
            "success": self.state == RalphState.COMMIT,
            "final_state": self.state,
            "metrics": self.metrics,
            "checkpoint_path": str(self._checkpoint()) if self._checkpoint_count > 0 else None,
            "error_log": self.error_log,
        }

    def stop(self) -> None:
        """Gracefully stop the RalphLoop."""
        self._running = False

    def reset(self) -> None:
        """Reset RalphLoop to initial state."""
        self.state = RalphState.PLAN
        self.task_index = 0
        self.retry_count = 0
        self.error_log = []
        self.metrics = RalphLoopMetrics()
        self._running = False
        self._checkpoint_count = 0
