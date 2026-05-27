"""Task Graph — Dependency-aware Task Management with Parallel Claiming.

This module replaces the simple sequential task queue with a dependency graph:
- Tasks declare explicit dependencies
- Multiple agents can claim independent tasks in parallel
- System autonomously claims available tasks
- Status tracking: PENDING → CLAIMED → COMPLETED/FAILED/BLOCKED

Key insight: Tasks should be claimed by agents, not assigned centrally.
The graph manages dependencies; agents self-select work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable
from threading import Lock
import uuid


class TaskStatus(Enum):
    """Task lifecycle states."""
    PENDING = auto()    # Waiting for dependencies
    READY = auto()      # Dependencies met, can be claimed
    CLAIMED = auto()    # Being worked on by an agent
    COMPLETED = auto()  # Successfully finished
    FAILED = auto()     # Worked but failed
    BLOCKED = auto()     # Dependencies not met, may unblock later


class RollbackStrategy(Enum):
    """What to do when a task fails."""
    ABORT = auto()           # Fail the whole workflow
    RETRY = auto()           # Retry this task once
    SKIP = auto()            # Skip this task, continue others
    DECOMPOSE = auto()       # Break into smaller subtasks


@dataclass
class TaskNode:
    """A task with dependency tracking and autonomous claiming.

    Attributes:
        id: Unique task identifier
        description: What this task does
        dependencies: Set of task IDs that must complete first
        dynamic_dependencies: Set of task IDs discovered during execution
        status: Current TaskStatus
        assigned_agent: Agent ID that claimed this task
        result: Final result dict (if completed/failed)
        created_at: When task was created
        claimed_at: When task was claimed
        completed_at: When task completed
        metadata: Arbitrary task metadata
        success_criteria: How to judge this task as complete (for VERIFY)
        rollback_strategy: What to do if this task fails
        estimated_context_cost: Estimated context budget % for this task
        priority: Priority 1-10 (higher = claimed first)
        starvation_level: How many claim cycles this task has waited
    """
    id: str
    description: str
    dependencies: set[str] = field(default_factory=set)
    dynamic_dependencies: set[str] = field(default_factory=set)
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: Optional[str] = None
    result: Optional[dict] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    # Plan Contract fields
    success_criteria: str = ""           # e.g., "All tests pass, no build errors"
    rollback_strategy: RollbackStrategy = RollbackStrategy.ABORT
    estimated_context_cost: float = 0.0  # Estimated % of context budget
    # Priority and starvation
    priority: int = 5                    # 1-10, default medium priority
    starvation_level: int = 0             # Increments each claim cycle not claimed

    @property
    def all_dependencies(self) -> set[str]:
        """All dependencies (static + dynamic)."""
        return self.dependencies | self.dynamic_dependencies

    @property
    def is_blocked(self) -> bool:
        """Check if this task is blocked by unmet dependencies.

        DEPRECATED: Use status == TaskStatus.PENDING with graph._can_claim(node)
        to determine if actually blocked. This property only checks local status.
        """
        return self.status == TaskStatus.PENDING

    @property
    def can_claim(self) -> bool:
        """Check if this task can be claimed (dependencies met, not claimed).

        NOTE: This is a local check only. TaskGraph._can_claim() does the
        authoritative check against actual dependency statuses.
        """
        if self.status != TaskStatus.PENDING:
            return False
        # Local check: assume dependencies can be satisfied
        # (Actual check happens in TaskGraph._can_claim)
        return True

    def compute_effective_priority(self) -> float:
        """Compute priority with starvation boost.

        Starvation increases effective priority by 0.5 per missed claim cycle,
        capped at +3.0 to prevent starvation from dominating.
        """
        return self.priority + min(self.starvation_level * 0.5, 3.0)

    def add_dynamic_dependency(self, task_id: str) -> None:
        """Add a dependency discovered during execution."""
        if task_id != self.id:  # Prevent self-dependency
            self.dynamic_dependencies.add(task_id)

    def claim(self, agent_id: str) -> bool:
        """Attempt to claim this task for an agent.

        Returns True if claimed successfully, False otherwise.
        """
        if not self.can_claim:
            return False

        self.status = TaskStatus.CLAIMED
        self.assigned_agent = agent_id
        self.claimed_at = datetime.now().isoformat()
        self.starvation_level = 0  # Reset starvation on claim
        return True

    def complete(self, result: Optional[dict] = None) -> None:
        """Mark task as completed with result."""
        self.status = TaskStatus.COMPLETED
        self.result = result or {}
        self.completed_at = datetime.now().isoformat()

    def fail(self, error: str) -> None:
        """Mark task as failed with error."""
        self.status = TaskStatus.FAILED
        self.result = {"error": error}
        self.completed_at = datetime.now().isoformat()

    def to_summary(self) -> str:
        """Get a brief task summary for logging/display."""
        deps = f"[deps: {', '.join(self.dependencies)}]" if self.dependencies else ""
        agent = f"@{self.assigned_agent}" if self.assigned_agent else ""
        cost = f"[cost: {self.estimated_context_cost:.0f}%)]" if self.estimated_context_cost else ""
        criteria = f"[criteria: {self.success_criteria[:30]}...]" if self.success_criteria else ""
        starve = f"[starve: {self.starvation_level}]" if self.starvation_level > 0 else ""
        priority = f"[pri: {self.priority}]" if self.priority != 5 else ""
        return f"{self.id} {agent} {self.status.name} {deps} {cost} {criteria} {starve} {priority}".strip()


@dataclass
class TaskClaimResult:
    """Result of a task claim attempt."""
    success: bool
    task: Optional[TaskNode] = None
    reason: str = ""


class TaskGraph:
    """Dependency-aware task graph with parallel claiming.

    Key features:
    - Declarative dependencies: task.dependencies = {"task_id", ...}
    - Autonomous claiming: agents call claim_next_available()
    - Parallel execution: multiple agents can work simultaneously
    - Dependency resolution: blocked tasks auto-unblock when deps complete

    Usage:
        graph = TaskGraph()

        # Add tasks (order doesn't matter — graph resolves deps)
        graph.add_task(TaskNode(id="init", description="Initialize project"))
        graph.add_task(TaskNode(id="api", description="Build API", dependencies={"init"}))
        graph.add_task(TaskNode(id="frontend", description="Build UI", dependencies={"api"}))

        # Agents claim available work
        agent1_task = graph.claim_next_available("agent-1")  # Returns "init"
        agent2_task = graph.claim_next_available("agent-2")  # Returns "api" (after init)

        # When task completes:
        graph.complete_task("init", result={"status": "ok"})
        # Now "api" and "frontend" may be unblocked
    """

    def __init__(self):
        self.nodes: dict[str, TaskNode] = {}
        self._claim_lock = Lock()
        self._callbacks: dict[str, list[Callable]] = {
            "on_claim": [],
            "on_complete": [],
            "on_fail": [],
            "on_unblock": []
        }

    def add_task(self, task: TaskNode) -> None:
        """Add a task to the graph."""
        self.nodes[task.id] = task

    def add_tasks(self, tasks: list[TaskNode]) -> None:
        """Add multiple tasks at once."""
        for task in tasks:
            self.add_task(task)

    def get_task(self, task_id: str) -> Optional[TaskNode]:
        """Get a task by ID."""
        return self.nodes.get(task_id)

    def claim_next_available(self, agent_id: str) -> TaskClaimResult:
        """Autonomous task claiming for parallel execution.

        Finds the highest-priority ready task and claims it for the agent.
        Thread-safe: uses lock to prevent double-claiming.
        Respects starvation: unclaimed tasks gradually increase in priority.

        Returns:
            TaskClaimResult with success/task/reason
        """
        with self._claim_lock:
            # Update starvation levels for unclaimed ready tasks
            self._bump_starvation()

            # Find all claimable tasks, sorted by effective priority (descending)
            claimable = [
                (node, node.compute_effective_priority())
                for node in self.nodes.values()
                if self._can_claim(node)
            ]
            claimable.sort(key=lambda x: x[1], reverse=True)  # Highest priority first

            if claimable:
                node = claimable[0][0]
                success = node.claim(agent_id)
                if success:
                    self._fire_callbacks("on_claim", node)
                    return TaskClaimResult(
                        success=True,
                        task=node,
                        reason=f"Claimed by {agent_id} (priority: {node.compute_effective_priority():.1f})"
                    )

            return TaskClaimResult(
                success=False,
                task=None,
                reason="No tasks available for claiming"
            )

    def _bump_starvation(self) -> None:
        """Increment starvation for all unclaimed ready tasks."""
        for node in self.nodes.values():
            if node.status == TaskStatus.PENDING and self._can_claim(node):
                node.starvation_level += 1

    def _can_claim(self, node: TaskNode) -> bool:
        """Check if a node can be claimed (dependencies met)."""
        if node.status != TaskStatus.PENDING:
            return False

        for dep_id in node.all_dependencies:
            dep = self.nodes.get(dep_id)
            if dep is None:
                # Dependency doesn't exist — treat as completed
                continue
            if dep.status == TaskStatus.FAILED:
                return False  # Block: failed dependency must be explicitly re-planned
            if dep.status != TaskStatus.COMPLETED:
                return False

        return True

    def get_ready_tasks(self) -> list[TaskNode]:
        """Return all tasks ready to execute (dependencies met)."""
        return [n for n in self.nodes.values() if self._can_claim(n)]

    def get_claimed_tasks(self) -> list[TaskNode]:
        """Return all tasks currently being worked on."""
        return [n for n in self.nodes.values() if n.status == TaskStatus.CLAIMED]

    def get_pending_tasks(self) -> list[TaskNode]:
        """Return all pending tasks (not yet ready)."""
        return [
            n for n in self.nodes.values()
            if n.status == TaskStatus.PENDING and not self._can_claim(n)
        ]

    def complete_task(self, task_id: str, result: Optional[dict] = None) -> bool:
        """Mark a task as completed and fire callbacks."""
        node = self.nodes.get(task_id)
        if node is None:
            return False

        node.complete(result)
        self._fire_callbacks("on_complete", node)

        # Check if any blocked tasks are now unblocked
        self._check_unblocked()

        return True

    def fail_task(self, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        node = self.nodes.get(task_id)
        if node is None:
            return False

        node.fail(error)
        self._fire_callbacks("on_fail", node)

        # Check if any blocked tasks are now unblocked
        self._check_unblocked()

        return True

    def _check_unblocked(self) -> None:
        """Check for tasks that were blocked but are now unblocked."""
        for node in self.nodes.values():
            if node.status == TaskStatus.PENDING and self._can_claim(node):
                self._fire_callbacks("on_unblock", node)

    def on(self, event: str, callback: Callable) -> None:
        """Register a callback for graph events.

        Events: on_claim, on_complete, on_fail, on_unblock
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _fire_callbacks(self, event: str, node: TaskNode) -> None:
        """Fire all callbacks for an event."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(node)
            except Exception:
                pass  # Don't let callbacks break the graph

    def get_stats(self) -> dict:
        """Get graph statistics."""
        statuses = {}
        for node in self.nodes.values():
            status = node.status.name
            statuses[status] = statuses.get(status, 0) + 1

        return {
            "total": len(self.nodes),
            "by_status": statuses,
            "ready": len(self.get_ready_tasks()),
            "claimed": len(self.get_claimed_tasks()),
            "pending": len(self.get_pending_tasks()),
        }

    def to_graphviz(self) -> str:
        """Generate Graphviz DOT representation for visualization."""
        lines = ["digraph task_graph {", "  rankdir=LR;"]

        for node in self.nodes.values():
            color = {
                TaskStatus.PENDING: "gray",
                TaskStatus.READY: "blue",
                TaskStatus.CLAIMED: "orange",
                TaskStatus.COMPLETED: "green",
                TaskStatus.FAILED: "red",
            }.get(node.status, "gray")

            label = f"{node.id}\\n{node.status.name}"
            if node.assigned_agent:
                label += f"\\n@{node.assigned_agent}"

            lines.append(f'  "{node.id}" [label="{label}", color={color}];')

        # Dependencies
        for node in self.nodes.values():
            for dep in node.dependencies:
                lines.append(f'  "{dep}" -> "{node.id}";')

        lines.append("}")
        return "\n".join(lines)

    def summary(self) -> str:
        """Get a human-readable graph summary."""
        lines = ["TaskGraph Status:"]

        for status in TaskStatus:
            nodes = [n for n in self.nodes.values() if n.status == status]
            if nodes:
                lines.append(f"  {status.name}: {len(nodes)}")
                for n in nodes:
                    lines.append(f"    - {n.to_summary()}")

        return "\n".join(lines)