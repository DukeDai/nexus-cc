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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from threading import Lock
from typing import Callable, Optional, Any

from .states import RalphState
from .transitions import (
    Transition,
    TransitionContext,
    TransitionTrigger,
    get_valid_transitions,
    get_abort_transition,
)
from .error_isolation import ShadowErrorTracker, ErrorIsolationStrategy
from .feedback_loop_integration import IntegratedFeedbackLoop
from .adaptive_reasoning import AdaptiveReasoningEngine
from .dynamic_reasoning import ReasoningProfile
from .task_graph import TaskGraph, TaskNode, TaskStatus


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
    total_turns: int = 0          # Total LLM turns across all phases
    context_tier_changes: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    states_visited: list[RalphState] = field(default_factory=list)

    def record_state(self, state: RalphState) -> None:
        self.states_visited.append(state)
        self.total_iterations += 1

    def add_turns(self, n: int) -> None:
        """Record LLM turns (called from executor after each phase)."""
        self.total_turns += n

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
        on_approval_request: Optional callback(type, description, details) -> bool.
                           Types: "commit", "context_threshold", "dangerous_command".
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
        on_approval_request: Optional[Callable[[str, str, dict], bool]] = None,
        speculative_agent_executor: Optional[Callable[..., dict[str, Any]]] = None,
        enable_feedback_loop: bool = True,
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
            on_approval_request: Callback(approval_type, description, details) -> bool.
                               Called before COMMIT to request user approval.
            speculative_agent_executor: Background executor for next-task PLAN
                while current ACT is running. Signature same as agent_executor.
                When provided, enables speculative planning to overlap
                next-task PLAN with current-task ACT execution.
        """
        self.task_queue = task_queue
        self.context_monitor = context_monitor
        self.checkpoint_dir = checkpoint_dir
        self.on_state_change = on_state_change
        self.on_escalation = on_escalation
        self.on_warning = on_warning
        self.agent_executor = agent_executor or self._default_agent_executor
        self.on_approval_request = on_approval_request
        self.speculative_agent_executor = speculative_agent_executor

        self.state: RalphState = RalphState.PLAN
        self.task_index: int = 0
        self.retry_count: int = 0
        self.error_log: list[str] = []
        self.metrics = RalphLoopMetrics()
        self._running: bool = False
        self._checkpoint_count: int = 0
        self._last_context_tier: ContextTier = ContextTier.PEAK
        self._context_threshold_approved: bool = False  # True once user approves DEGRADING
        self._pending_dangerous_commands: list[str] = []
        # Speculative planning state
        self._speculative_future: Any = None
        self._speculative_lock: Lock = Lock()
        self._speculative_spec: str | None = None  # Cached next-task spec
        self._speculative_completed: bool = False   # Track if speculative PLAN finished
        self._speculative_workers: list = []       # Track speculative worker threads
        # Error isolation state — passed to ShadowErrorTracker for EVICT strategy
        self._current_context_messages: list[dict] = []
        # Error isolation: store failures separately, never pollute context
        self.error_tracker = ShadowErrorTracker(strategy=ErrorIsolationStrategy.SHADOW)
        # Feedback loop: dynamic reasoning intensity adjustment
        self.feedback_loop: Optional[IntegratedFeedbackLoop] = None
        if enable_feedback_loop:
            self.feedback_loop = IntegratedFeedbackLoop(
                reasoning_engine=AdaptiveReasoningEngine()
            )
        # Task graph: dependency-aware parallel claiming (replaces flat task_index)
        self.task_graph = TaskGraph()
        for i, task in enumerate(task_queue):
            node = TaskNode(
                id=task.get("id", f"task-{i}"),
                description=task.get("description", ""),
                priority=task.get("priority", 5),
            )
            # Set explicit dependencies if declared
            deps = task.get("dependencies", [])
            if isinstance(deps, list):
                for dep_id in deps:
                    node.dependencies.add(dep_id)
            self.task_graph.add_task(node)

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
        """Build TransitionContext for guard evaluation.

        Uses ShadowErrorTracker to provide clean decision context —
        error traces stay isolated, only recovery hints reach the LLM.
        """
        error_ctx = self.error_tracker.get_decision_context()
        return TransitionContext(
            retry_count=self.retry_count,
            context_usage_percent=self.context_usage,
            tasks_remaining=len(self.task_queue) - self.task_index,
            current_error=error_ctx.recovery_hint,  # Hint only, not full trace
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

    def _handle_exception(self, exc: Exception, phase: RalphState, tool_calls: list[dict] | None = None) -> None:
        """Handle exception during state execution.

        Records error in ShadowErrorTracker (isolated from LLM context) and
        increments retry count. The orchestrator sees only the recovery hint.
        """
        error_msg = f"[{datetime.now().isoformat()}] {type(exc).__name__}: {exc}"
        self.error_log.append(error_msg)
        self.retry_count += 1
        self.metrics.total_retries += 1

        # Populate context messages for EVICT strategy before recording failure
        if self.error_tracker.strategy == ErrorIsolationStrategy.EVICT:
            self._current_context_messages = self._get_current_context_snapshot()

        # Record in ShadowErrorTracker — keeps error traces out of LLM context
        self.error_tracker.record_failure(
            phase=phase.value,
            tool_calls=tool_calls or [],
            error=str(exc),
            context_messages=self._current_context_messages if self.error_tracker.strategy == ErrorIsolationStrategy.EVICT else None,
        )

    def _get_current_context_snapshot(self) -> list[dict]:
        """Capture current LLM-visible messages for potential eviction on error."""
        if self._current_context:
            return self._current_context.get_messages_for_llm()
        return []

    def _notify_feedback(self, event_type: str, phase: RalphState, result: dict) -> None:
        """Send phase events to feedback loop for reasoning intensity adjustment."""
        if self.feedback_loop is None:
            return
        turn_count = result.get("turn_count", 0)
        success = result.get("success", False)
        self.feedback_loop.set_current_phase(phase.value)
        if event_type == "PHASE_COMPLETE":
            self.feedback_loop.on_phase_complete(phase.value, turn_count, success)
        elif event_type == "TASK_FAILED":
            task_id = result.get("task_id", f"{phase.value}-{self.task_index}")
            self.feedback_loop.on_task_failed(task_id, result.get("error", "unknown"))
        # Update context tier in feedback loop
        self.feedback_loop.on_context_degrading(self.context_usage)

    def _get_current_reasoning_profile(self) -> ReasoningProfile:
        """Get current reasoning profile from feedback loop for execution guidance."""
        if self.feedback_loop is None:
            from .dynamic_reasoning import ReasoningProfile, ReasoningIntensity
            return ReasoningProfile(intensity=ReasoningIntensity.MODERATE, max_turns=3)
        return self.feedback_loop.get_current_reasoning_profile()

    def _check_context_tier_warnings(self) -> None:
        """Emit warnings and request approval if context tier changes to DEGRADING."""
        tier = self.context_tier

        # Request approval when entering DEGRADING tier (50-70%)
        if tier == ContextTier.DEGRADING and self._last_context_tier != ContextTier.DEGRADING:
            if self.on_approval_request and not self._context_threshold_approved:
                # Block and wait for user decision
                approved = self.on_approval_request(
                    "context_threshold",
                    f"Context budget {self.context_usage:.1f}% — entering DEGRADING mode. Continue?",
                    {"tier": tier.name, "usage": self.context_usage}
                )
                self._context_threshold_approved = approved
                if not approved:
                    # User rejected - initiate graceful stop
                    self._running = False

        if tier.should_warn() and self.on_warning:
            self.on_warning(
                tier,
                f"Context budget {self.context_usage:.1f}% — "
                "entering DEGRADING mode. Frontmatter-only reads, minimal inlining."
            )

        self._last_context_tier = tier

    def _execute_state(self) -> TransitionTrigger:
        """Execute current state and return trigger for next transition.

        Each state method returns a TransitionTrigger indicating the
        outcome of the state execution.
        """
        task = self.task_queue[self.task_index] if self.task_index < len(self.task_queue) else {}

        if self.state == RalphState.DECOMPOSE:
            result = self.agent_executor(task, RalphState.DECOMPOSE)
            if result.get("success"):
                return TransitionTrigger.DECOMPOSE_COMPLETE
            else:
                self._handle_exception(Exception(result.get("error", "Decompose failed")), RalphState.DECOMPOSE)
                return TransitionTrigger.VERIFICATION_FAILED  # reuse — triggers retry or escalation

        elif self.state == RalphState.PLAN:
            # Use pre-computed spec from speculative planning if available
            planned_spec = self._speculative_spec or task.get("spec_md", "")
            if self._speculative_spec is not None:
                task = {**task, "spec_md": self._speculative_spec}
                self._speculative_spec = None  # Consume the cached spec
            result = self.agent_executor(task, RalphState.PLAN)
            if result.get("success"):
                self.retry_count = 0  # Reset on successful plan
                # Track plan vs actual for feedback loop
                actual_spec = result.get("spec_md", task.get("spec_md", ""))
                if self.feedback_loop:
                    self.feedback_loop.on_plan_update(planned_spec, actual_spec)
                return TransitionTrigger.SPEC_VALID
            else:
                self._handle_exception(Exception(result.get("error", "Plan failed")), RalphState.PLAN)
                return TransitionTrigger.VERIFICATION_FAILED

        elif self.state == RalphState.ACT:
            # Start speculative planning BEFORE executor runs — overlaps with ACT execution
            self._speculative_start(task)
            result = self.agent_executor(task, RalphState.ACT)
            if result.get("success"):
                self._notify_feedback("PHASE_COMPLETE", RalphState.ACT, result)
                return TransitionTrigger.IMPLEMENTATION_COMPLETE
            else:
                self._notify_feedback("TASK_FAILED", RalphState.ACT, result)
                self._handle_exception(Exception(result.get("error", "Act failed")), RalphState.ACT)
                return TransitionTrigger.VERIFICATION_FAILED

        elif self.state == RalphState.VERIFY:
            result = self.agent_executor(task, RalphState.VERIFY)

            # Check for dangerous commands
            dangerous = result.get("dangerous_commands", [])
            if dangerous and self.on_approval_request:
                approved = self.on_approval_request(
                    "dangerous_command",
                    f"Dangerous commands detected: {', '.join(dangerous)}",
                    {"commands": dangerous}
                )
                if not approved:
                    self._running = False
                    return TransitionTrigger.VERIFICATION_FAILED

            if result.get("success"):
                self._notify_feedback("PHASE_COMPLETE", RalphState.VERIFY, result)
                return TransitionTrigger.VERIFICATION_PASSED
            else:
                self._notify_feedback("TASK_FAILED", RalphState.VERIFY, result)
                self._handle_exception(Exception(result.get("error", "Verify failed")), RalphState.VERIFY)
                if self.retry_count > self.MAX_RETRIES:
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

    # ─── Speculative Planning ─────────────────────────────────────────────────

    def _speculative_start(self, current_task: dict[str, Any]) -> None:
        """Launch competitive background PLAN for next task while current ACT runs.

        Multiple workers race to complete the next task's PLAN phase.
        First worker to finish provides the spec; others are cancelled.
        This reduces latency compared to single speculative worker.

        Called at the start of ACT. Caches the resulting spec so it's
        ready when we reach the next task's PLAN state.
        """
        spec_exec = self.speculative_agent_executor
        if spec_exec is None:
            return

        next_index = self.task_index + 1
        if next_index >= len(self.task_queue):
            return  # No next task

        next_task = self.task_queue[next_index]

        with self._speculative_lock:
            # Cancel any in-progress speculative work
            self._speculative_spec = None
            self._speculative_completed = False

            # Cancel existing worker threads
            for worker in self._speculative_workers:
                try:
                    worker.cancel()
                except Exception:
                    pass
            self._speculative_workers.clear()

            def background_plan(worker_id: int) -> None:
                """Background worker that races to complete PLAN.

                Uses a shared lock to update _speculative_spec on completion.
                Only the first worker to finish gets to set the spec.
                """
                try:
                    result = spec_exec(next_task, RalphState.PLAN)
                    with self._speculative_lock:
                        # Only set if not already set by another worker
                        if self._speculative_spec is None and result.get("success"):
                            spec = result.get("spec_md", "")
                            self._speculative_spec = spec
                            self._speculative_completed = True
                except Exception:
                    pass  # Silently ignore speculative failures

            # Launch multiple workers for competitive planning
            # More workers = higher chance of fast completion, but more resource usage
            num_workers = min(2, self._get_speculative_worker_count())
            pool = ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="speculative_")

            for i in range(num_workers):
                future = pool.submit(background_plan, i)
                self._speculative_workers.append(future)

            pool.shutdown(wait=False)

    def _get_speculative_worker_count(self) -> int:
        """Determine how many speculative workers to launch.

        Based on task complexity and context budget.
        Simple tasks need fewer workers; complex tasks benefit from competition.
        """
        if not self.task_queue:
            return 1

        # Check next task complexity in metadata
        next_task = self.task_queue[self.task_index + 1] if self.task_index + 1 < len(self.task_queue) else {}
        complexity = next_task.get("metadata", {}).get("complexity", "MODERATE")

        if complexity == "COMPLEX":
            return 3  # Complex tasks benefit more from competition
        elif complexity == "SIMPLE":
            return 1  # Simple tasks don't need competition
        return 2  # Default

    def _speculative_get_spec(self) -> Optional[str]:
        """Get the cached speculative spec if available."""
        with self._speculative_lock:
            return self._speculative_spec

    def _speculative_is_ready(self) -> bool:
        """Check if speculative planning completed."""
        with self._speculative_lock:
            return self._speculative_completed and self._speculative_spec is not None

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

                # Checkpoint periodically (after first checkpoint exists)
                if self._checkpoint_count >= 1 and self.metrics.total_iterations % self.CHECKPOINT_INTERVAL == 0:
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
                    # Request approval before final commit
                    if self.on_approval_request:
                        approved = self.on_approval_request(
                            "commit",
                            "Approve commit of all changes?",
                            {"task_count": len(self.task_queue)}
                        )
                        if not approved:
                            # Reject - stop without committing
                            self._running = False
                            return {
                                "success": False,
                                "outcome": "early",
                                "tasks_done": self.task_index,
                                "tasks_total": len(self.task_queue),
                                "final_state": self.state,
                                "metrics": self.metrics,
                                "checkpoint_path": str(self._checkpoint()) if self._checkpoint_count >= 1 else None,
                                "error_log": self.error_log + ["Commit rejected by user"],
                            }
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

        # Determine outcome tier
        tasks_done = self.task_index
        tasks_total = len(self.task_queue)
        if self.state == RalphState.COMMIT:
            outcome = "full"
        elif tasks_done > 0 and tasks_done < tasks_total:
            outcome = "partial"  # Some tasks completed before stopping
        elif self.state == RalphState.ABORT:
            outcome = "aborted"
        else:
            outcome = "early"  # Stopped before meaningful progress

        return {
            "success": self.state == RalphState.COMMIT,
            "outcome": outcome,
            "tasks_done": tasks_done,
            "tasks_total": tasks_total,
            "final_state": self.state,
            "metrics": self.metrics,
            "checkpoint_path": str(self._checkpoint()) if self._checkpoint_count >= 1 else None,
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
        self._last_context_tier = ContextTier.PEAK
        self._context_threshold_approved = False
        self._pending_dangerous_commands = []
