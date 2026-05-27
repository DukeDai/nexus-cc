"""Harness Integration — Unified RalphLoop Agent System.

This module integrates all harness enhancements into a unified system:
1. Error Isolation — failed trajectories don't pollute context
2. Phase Isolation — context partitioned by phase with compression
3. Task Graph — dependency-aware parallel task execution
4. Dynamic Reasoning — adaptive effort based on signals
5. Feedback Loop — async notifications and progress tracking

Usage:
    harness = AgentHarness(project_root=Path("."))
    result = harness.run_task("Build a REST API for todos")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Callable
import threading

from .states import RalphState
from .error_isolation import ShadowErrorTracker, ErrorIsolationStrategy, ErrorDecisionContext
from .phase_isolation import IsolatedContextManager, PhaseContext
from .task_graph import TaskGraph, TaskNode, TaskStatus, TaskClaimResult
from .dynamic_reasoning import DynamicReasoningEngine, ReasoningProfile, ReasoningSignals, ReasoningIntensity
from .feedback_loop import FeedbackLoop, FeedbackEventType


@dataclass
class HarnessConfig:
    """Configuration for the Agent Harness."""
    # Context settings
    context_window: int = 100000
    compress_at_budget: float = 50.0
    evict_at_budget: float = 70.0

    # Error isolation
    error_strategy: ErrorIsolationStrategy = ErrorIsolationStrategy.SHADOW
    max_trajectories: int = 50

    # Task graph
    max_parallel_tasks: int = 3
    enable_parallel_claiming: bool = True

    # Dynamic reasoning
    default_max_turns: int = 20

    # Feedback
    webhook_url: Optional[str] = None
    enable_console_feedback: bool = True


@dataclass
class HarnessMetrics:
    """Runtime metrics for the harness."""
    total_iterations: int = 0
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    context_compressions: int = 0
    reasoning_intensity_changes: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "total_iterations": self.total_iterations,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "context_compressions": self.context_compressions,
            "reasoning_intensity_changes": self.reasoning_intensity_changes,
            "duration_seconds": (self.end_time - self.start_time).total_seconds() if self.start_time and self.end_time else None
        }


class AgentHarness:
    """Unified agent harness integrating all enhancements.

    This is the main entry point for running agent tasks with:
    - Isolated error handling (no context pollution)
    - Phase-based context management with compression
    - Dependency-aware task graph with parallel execution
    - Dynamic reasoning intensity adjustment
    - Async feedback and progress notifications

    Usage:
        harness = AgentHarness()
        result = harness.run_task("Build a todo API")
    """

    def __init__(
        self,
        config: Optional[HarnessConfig] = None,
        workdir: Optional[Path] = None
    ):
        self.config = config or HarnessConfig()
        self.workdir = workdir or Path.cwd()

        # Core components
        self.error_tracker = ShadowErrorTracker(
            strategy=self.config.error_strategy,
            max_trajectories=self.config.max_trajectories
        )
        self.context_manager = IsolatedContextManager(
            context_window=self.config.context_window
        )
        self.task_graph = TaskGraph()
        self.reasoning_engine = DynamicReasoningEngine()
        self.feedback = FeedbackLoop(
            webhook_url=self.config.webhook_url,
            enable_console=self.config.enable_console_feedback
        )

        # Metrics
        self.metrics = HarnessMetrics()

        # Callbacks
        self._on_task_complete: list[Callable] = []
        self._on_error: list[Callable] = []

        # Lock for thread safety
        self._lock = threading.Lock()

    # ─── Task Execution ────────────────────────────────────────────────────────

    def run_task(self, task: str | dict) -> dict[str, Any]:
        """Run a single task through the harness.

        Args:
            task: Task description (string) or dict with 'description', 'dependencies', etc.

        Returns:
            Result dict with success, result, metrics
        """
        if isinstance(task, str):
            task = {"description": task, "id": self._generate_id()}

        # Create task node
        task_node = TaskNode(
            id=task.get("id", self._generate_id()),
            description=task.get("description", str(task)),
            dependencies=set(task.get("dependencies", [])),
            metadata=task.get("metadata", {})
        )

        # Add to graph
        self.task_graph.add_task(task_node)

        # Track
        self.metrics.total_tasks += 1
        if not self.metrics.start_time:
            self.metrics.start_time = datetime.now()

        # Compute initial reasoning profile
        complexity = task.get("complexity", "MODERATE")
        signals = ReasoningSignals(task_complexity=complexity)
        profile = self.reasoning_engine.compute_profile(signals)

        # Enter PLAN phase context
        ctx = self.context_manager.enter_phase("PLAN")

        # Run task through state machine
        try:
            result = self._execute_task_node(task_node, profile)

            if result.get("success"):
                self.task_graph.complete_task(task_node.id, result)
                self.metrics.completed_tasks += 1
            else:
                self.task_graph.fail_task(task_node.id, result.get("error", "Unknown"))
                self.metrics.failed_tasks += 1

            return result

        except Exception as e:
            error_result = {
                "success": False,
                "error": str(e),
                "task_id": task_node.id
            }
            self.task_graph.fail_task(task_node.id, str(e))
            self.metrics.failed_tasks += 1

            # Record in error tracker
            self.error_tracker.record_failure(
                phase="PLAN",
                tool_calls=[],
                error=str(e)
            )

            return error_result

        finally:
            self.metrics.total_iterations += 1
            self.feedback.on_metrics_update(self.metrics.to_dict())

    def run_tasks(self, tasks: list[dict]) -> list[dict[str, Any]]:
        """Run multiple tasks with dependency-aware parallel execution.

        Args:
            tasks: List of task dicts with 'description', optional 'dependencies'

        Returns:
            List of result dicts
        """
        # Add all tasks to graph
        for task in tasks:
            task_node = TaskNode(
                id=task.get("id", self._generate_id()),
                description=task.get("description", str(task)),
                dependencies=set(task.get("dependencies", []))
            )
            self.task_graph.add_task(task_node)

        results = []
        agents = [f"agent-{i}" for i in range(self.config.max_parallel_tasks)]

        # Claim and execute tasks in parallel
        while True:
            # Try to claim tasks for each agent
            claimed = False
            for agent_id in agents:
                claim_result = self.task_graph.claim_next_available(agent_id)
                if claim_result.success and claim_result.task:
                    claimed = True
                    # Execute task asynchronously would go here
                    # For now, execute synchronously
                    result = self._execute_task_node(claim_result.task, self.reasoning_engine.current_profile)
                    results.append(result)

                    if result.get("success"):
                        self.task_graph.complete_task(claim_result.task.id, result)
                        self.metrics.completed_tasks += 1
                    else:
                        self.task_graph.fail_task(claim_result.task.id, result.get("error", ""))
                        self.metrics.failed_tasks += 1

                    self.feedback.on_task_completed(claim_result.task.id, result)

            if not claimed:
                break  # No more tasks to claim

        return results

    def _execute_task_node(self, node: TaskNode, profile: ReasoningProfile) -> dict[str, Any]:
        """Execute a single task node through the state machine."""
        # Notify task claimed
        self.feedback.on_task_claimed(node.id, node.assigned_agent or "main")

        # Enter phases
        for phase in [RalphState.PLAN, RalphState.ACT, RalphState.VERIFY, RalphState.REFLECT]:
            ctx = self.context_manager.enter_phase(phase.name)

            # Compute profile for this phase
            signals = ReasoningSignals(
                task_complexity=node.metadata.get("complexity", "MODERATE"),
                context_budget_percent=self.context_manager.estimate_budget()
            )
            phase_profile = self.reasoning_engine.compute_profile(signals)

            # Execute phase (simplified - real implementation would call LLM)
            phase_success = self._execute_phase(phase, node, phase_profile)

            # Update reasoning engine with phase outcome
            self.reasoning_engine.on_phase_complete(phase.name, phase_success, turn_count=1)

            self.feedback.on_phase_complete(phase.name, turn_count=1, success=phase_success)

            if not phase_success:
                # Record error in isolation
                hint = self.error_tracker.record_failure(
                    phase=phase.name,
                    tool_calls=[],
                    error=f"Phase {phase.name} failed"
                )

                # Check error rate from reasoning engine (updated by on_phase_complete)
                if self.reasoning_engine._error_rate > 0.5:
                    # High error rate → switch to VERIFY mode
                    self.reasoning_engine._profile = ReasoningProfile(
                        intensity=ReasoningIntensity.VERIFY,
                        max_turns=30
                    )

                return {"success": False, "error": f"{phase.name} failed", "recovery_hint": hint}

        return {"success": True, "task_id": node.id, "result": {}}

    def _execute_phase(self, phase: RalphState, node: TaskNode, profile: ReasoningProfile) -> bool:
        """Execute a single phase. Returns success.

        NOTE: This is a STUB. Real implementation would call run_agent_loop()
        from agent_loop.py with proper LLM client and tool executor injection.
        """
        # TODO: Inject actual LLM client and tool executor
        # This requires access to llm/client.py and agent_loop.py
        return True

    # ─── Error Management ──────────────────────────────────────────────────────

    def get_decision_context(self) -> ErrorDecisionContext:
        """Get clean decision context for orchestrator."""
        return self.error_tracker.get_decision_context()

    def record_failure(
        self,
        phase: str,
        tool_calls: list[dict],
        error: str
    ) -> str:
        """Record a failure in isolation."""
        return self.error_tracker.record_failure(phase, tool_calls, error)

    # ─── Context Management ───────────────────────────────────────────────────

    def compress_context(self) -> None:
        """Trigger context compression if needed."""
        result = self.context_manager.compress_if_needed()
        if result:
            self.metrics.context_compressions += 1
            self.feedback.on_checkpoint({
                "type": "compression",
                "original_size": result.original_size,
                "compressed_size": result.compressed_size,
                "phases": result.phases_compressed
            })

    def get_context_stats(self) -> dict:
        """Get context management statistics."""
        return self.context_manager.get_stats()

    # ─── Task Graph ──────────────────────────────────────────────────────────

    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get status of a task."""
        node = self.task_graph.get_task(task_id)
        return node.status if node else None

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get all tasks ready to execute."""
        return self.task_graph.get_ready_tasks()

    # ─── Reasoning ───────────────────────────────────────────────────────────

    def get_current_profile(self) -> ReasoningProfile:
        """Get current reasoning profile."""
        return self.reasoning_engine.current_profile

    def adjust_reasoning(self, signals: ReasoningSignals) -> ReasoningProfile:
        """Adjust reasoning based on signals."""
        profile = self.reasoning_engine.compute_profile(signals)
        if profile.intensity != self.reasoning_engine.current_profile.intensity:
            self.metrics.reasoning_intensity_changes += 1
        return profile

    # ─── Feedback ────────────────────────────────────────────────────────────

    def on_task_complete(self, callback: Callable) -> None:
        """Register a task completion callback."""
        self._on_task_complete.append(callback)

    def on_error(self, callback: Callable) -> None:
        """Register an error callback."""
        self._on_error.append(callback)

    # ─── Utilities ───────────────────────────────────────────────────────────

    def _generate_id(self) -> str:
        """Generate a short unique ID."""
        import uuid
        return str(uuid.uuid4())[:8]

    def get_stats(self) -> dict:
        """Get full harness statistics."""
        return {
            "metrics": self.metrics.to_dict(),
            "context": self.get_context_stats(),
            "task_graph": self.task_graph.get_stats(),
            "reasoning": self.reasoning_engine.get_stats(),
            "feedback": self.feedback.get_stats(),
            "errors": {
                "total_failures": self.error_tracker.get_decision_context().trajectory_count,
                "recent_failures": self.error_tracker.get_decision_context().recent_failures
            }
        }

    def summary(self) -> str:
        """Get a human-readable harness summary."""
        lines = [
            "═══ Agent Harness Status ═══",
            f"Tasks: {self.metrics.completed_tasks}/{self.metrics.total_tasks} completed",
            f"Iterations: {self.metrics.total_iterations}",
            f"Context: {self.context_manager.estimate_budget():.1f}%",
            f"Reasoning: {self.reasoning_engine.current_profile.intensity.name}",
            f"Errors: {self.error_tracker.get_decision_context().trajectory_count} recorded"
        ]
        return "\n".join(lines)