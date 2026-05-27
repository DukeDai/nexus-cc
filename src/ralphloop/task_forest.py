"""Task Forest — Multi-level Hierarchical Task Management.

This module replaces flat TaskGraph with three-level hierarchy:
- TaskEpic: user-facing deliverables containing stories
- TaskStory: clear acceptance criteria containing tasks
- TaskNode: executable units with estimated/actual complexity

Key features:
- Adaptive scheduling based on retry_history + complexity
- Dependency inference engine
- Hierarchical progress tracking
- Cross-story parallel execution

Why Epic → Story → Task:
- Epic gives user progress visibility (交付感)
- Story defines clear boundaries
- Task is the execution unit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable
from threading import Lock
import uuid
import time


class EpicStatus(Enum):
    """Epic lifecycle states."""
    PENDING = auto()    # Not yet started
    IN_PROGRESS = auto()  # At least one story in progress
    COMPLETED = auto()  # All stories completed
    BLOCKED = auto()    # All stories blocked
    ABANDONED = auto()  # User abandoned


class StoryStatus(Enum):
    """Story lifecycle states."""
    PENDING = auto()
    READY = auto()      # Dependencies met
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()
    BLOCKED = auto()    # Dependencies not met


class TaskStatus(Enum):
    """Task lifecycle states."""
    PENDING = auto()
    READY = auto()
    CLAIMED = auto()
    COMPLETED = auto()
    FAILED = auto()
    BLOCKED = auto()


class RollbackStrategy(Enum):
    """What to do when a task fails."""
    ABORT = auto()
    RETRY = auto()
    SKIP = auto()
    DECOMPOSE = auto()


# ─── Task Hierarchy ────────────────────────────────────────────────────────────


@dataclass
class TaskNode:
    """A single executable task (leaf in hierarchy).

    Same as original TaskNode but aware of parent Story.
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
    success_criteria: str = ""
    rollback_strategy: RollbackStrategy = RollbackStrategy.ABORT
    estimated_context_cost: float = 0.0
    priority: int = 5
    starvation_level: int = 0
    parent_story_id: Optional[str] = None  # NEW: parent reference

    @property
    def all_dependencies(self) -> set[str]:
        return self.dependencies | self.dynamic_dependencies

    @property
    def is_blocked(self) -> bool:
        return self.status == TaskStatus.PENDING

    @property
    def can_claim(self) -> bool:
        if self.status != TaskStatus.PENDING:
            return False
        return True

    def compute_effective_priority(self) -> float:
        """Compute priority with starvation boost and complexity adjustment."""
        base = self.priority + min(self.starvation_level * 0.5, 3.0)

        # Adjust for actual complexity (from history)
        actual = self.metadata.get("actual_complexity")
        if actual and actual > 0:
            # High complexity tasks get boost
            base += min(actual * 0.1, 1.0)

        return base

    def add_dynamic_dependency(self, task_id: str) -> None:
        if task_id != self.id:
            self.dynamic_dependencies.add(task_id)

    def claim(self, agent_id: str) -> bool:
        if not self.can_claim:
            return False
        self.status = TaskStatus.CLAIMED
        self.assigned_agent = agent_id
        self.claimed_at = datetime.now().isoformat()
        self.starvation_level = 0
        return True

    def complete(self, result: Optional[dict] = None) -> None:
        self.status = TaskStatus.COMPLETED
        self.result = result or {}
        self.completed_at = datetime.now().isoformat()

        # Record actual duration for learning
        if self.claimed_at:
            start = datetime.fromisoformat(self.claimed_at)
            duration = (datetime.now() - start).total_seconds()
            self.metadata["actual_duration_s"] = duration

    def fail(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.result = {"error": error}
        self.completed_at = datetime.now().isoformat()

        # Record failure for adaptive scheduling
        failures = self.metadata.get("failure_count", 0) + 1
        self.metadata["failure_count"] = failures
        self.metadata["last_failure"] = error[:100]

    def to_summary(self) -> str:
        deps = f"[deps: {', '.join(self.dependencies)}]" if self.dependencies else ""
        agent = f"@{self.assigned_agent}" if self.assigned_agent else ""
        cost = f"[cost: {self.estimated_context_cost:.0f}%)]" if self.estimated_context_cost else ""
        starve = f"[starve: {self.starvation_level}]" if self.starvation_level > 0 else ""
        priority = f"[pri: {self.priority}]" if self.priority != 5 else ""
        return f"{self.id} {agent} {self.status.name} {deps} {cost} {starve} {priority}".strip()


@dataclass
class TaskStory:
    """A user-facing deliverable containing tasks.

    Stories are the unit of parallelization — multiple stories
    can be in progress simultaneously from different epics.
    """
    id: str
    description: str
    tasks: list[TaskNode] = field(default_factory=list)
    acceptance_criteria: str = ""
    status: StoryStatus = StoryStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    parent_epic_id: Optional[str] = None
    priority: int = 5

    @property
    def all_task_ids(self) -> set[str]:
        """All task IDs in this story."""
        return {t.id for t in self.tasks}

    @property
    def completed_tasks(self) -> list[TaskNode]:
        return [t for t in self.tasks if t.status == TaskStatus.COMPLETED]

    @property
    def failed_tasks(self) -> list[TaskNode]:
        return [t for t in self.tasks if t.status == TaskStatus.FAILED]

    @property
    def progress_percent(self) -> float:
        """Overall story progress."""
        if not self.tasks:
            return 0.0
        completed = len(self.completed_tasks)
        return (completed / len(self.tasks)) * 100

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get all tasks ready to execute."""
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue

            # Check dependencies
            deps_met = all(
                self._get_task_status(dep_id) == TaskStatus.COMPLETED
                for dep_id in task.all_dependencies
            )
            if deps_met:
                ready.append(task)
        return ready

    def _get_task_status(self, task_id: str) -> TaskStatus:
        """Get status of a task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task.status
        return TaskStatus.PENDING

    def update_status(self) -> None:
        """Update story status based on task statuses."""
        if not self.tasks:
            self.status = StoryStatus.PENDING
            return

        statuses = {t.status for t in self.tasks}

        if TaskStatus.FAILED in statuses:
            if statuses == {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                self.status = StoryStatus.FAILED
            else:
                self.status = StoryStatus.IN_PROGRESS
        elif all(s == TaskStatus.COMPLETED for s in statuses):
            self.status = StoryStatus.COMPLETED
            self.completed_at = datetime.now().isoformat()
        elif any(t.status == TaskStatus.CLAIMED for t in self.tasks):
            # A CLAIMED task means an agent is working on it
            self.status = StoryStatus.IN_PROGRESS
            if not self.started_at:
                self.started_at = datetime.now().isoformat()
        elif any(t.status == TaskStatus.PENDING for t in self.tasks):
            self.status = StoryStatus.READY
        else:
            self.status = StoryStatus.PENDING

    def to_summary(self) -> str:
        completed = len(self.completed_tasks)
        total = len(self.tasks)
        status = self.status.name
        return f"{self.id}: {status} ({completed}/{total} tasks)"


@dataclass
class TaskEpic:
    """A high-level user-facing deliverable.

    Epics aggregate stories and provide progress visibility.
    """
    id: str
    description: str
    stories: list[TaskStory] = field(default_factory=list)
    status: EpicStatus = EpicStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    priority: int = 5

    @property
    def completed_stories(self) -> list[TaskStory]:
        return [s for s in self.stories if s.status == StoryStatus.COMPLETED]

    @property
    def progress_percent(self) -> float:
        """Overall epic progress across all stories."""
        if not self.stories:
            return 0.0
        completed = len(self.completed_stories)
        return (completed / len(self.stories)) * 100

    @property
    def total_tasks(self) -> int:
        return sum(len(s.tasks) for s in self.stories)

    @property
    def completed_tasks(self) -> int:
        return sum(len(s.completed_tasks) for s in self.stories)

    def update_status(self) -> None:
        """Update epic status based on story statuses."""
        if not self.stories:
            self.status = EpicStatus.PENDING
            return

        statuses = {s.status for s in self.stories}

        if EpicStatus.ABANDONED in statuses:
            self.status = EpicStatus.ABANDONED
        elif all(s == StoryStatus.COMPLETED for s in statuses):
            self.status = EpicStatus.COMPLETED
            self.completed_at = datetime.now().isoformat()
        elif any(s.status == StoryStatus.IN_PROGRESS for s in self.stories):
            self.status = EpicStatus.IN_PROGRESS
            if not self.started_at:
                self.started_at = datetime.now().isoformat()
        elif all(s in {StoryStatus.PENDING, StoryStatus.BLOCKED, StoryStatus.COMPLETED} for s in statuses):
            # All pending or completed — check if blocked
            if any(s == StoryStatus.BLOCKED for s in statuses):
                self.status = EpicStatus.BLOCKED
            else:
                self.status = EpicStatus.PENDING
        else:
            self.status = EpicStatus.PENDING

    def to_summary(self) -> str:
        completed = len(self.completed_stories)
        total = len(self.stories)
        return f"{self.id}: {self.status.name} ({completed}/{total} stories, {self.completed_tasks}/{self.total_tasks} tasks)"


@dataclass
class TaskClaimResult:
    """Result of a task claim attempt."""
    success: bool
    task: Optional[TaskNode] = None
    reason: str = ""


# ─── Task Forest ─────────────────────────────────────────────────────────────


class TaskForest:
    """Hierarchical task forest with Epic → Story → Task structure.

    Replaces flat TaskGraph with multi-level hierarchy for better:
    - User progress visibility
    - Semantic organization
    - Adaptive scheduling

    Usage:
        forest = TaskForest()

        # Create epic
        epic = forest.add_epic("User Authentication", priority=8)

        # Add stories to epic
        story = forest.add_story(epic.id, "JWT Implementation", acceptance_criteria="All auth tests pass")
        story2 = forest.add_story(epic.id, "Session Management", ...)

        # Add tasks to story
        forest.add_task(story.id, "Implement RS256 signing", priority=7)
        forest.add_task(story.id, "Add token refresh", ...)

        # Agents claim available work
        task = forest.claim_next_available("agent-1")  # Highest priority ready task
    """

    def __init__(self):
        self.epics: dict[str, TaskEpic] = {}
        self.stories: dict[str, TaskStory] = {}
        self.tasks: dict[str, TaskNode] = {}  # Flat index for fast lookup
        self._claim_lock = Lock()
        self._callbacks: dict[str, list[Callable]] = {
            "on_claim": [],
            "on_complete": [],
            "on_fail": [],
            "on_unblock": [],
            "on_story_complete": [],
            "on_epic_complete": [],
        }

    # ─── Epic Operations ──────────────────────────────────────────────────────

    def add_epic(self, description: str, priority: int = 5) -> TaskEpic:
        """Create a new epic."""
        epic_id = f"epic_{uuid.uuid4().hex[:8]}"
        epic = TaskEpic(
            id=epic_id,
            description=description,
            priority=priority,
        )
        self.epics[epic_id] = epic
        return epic

    def get_epic(self, epic_id: str) -> Optional[TaskEpic]:
        return self.epics.get(epic_id)

    # ─── Story Operations ──────────────────────────────────────────────────────

    def add_story(
        self,
        epic_id: str,
        description: str,
        acceptance_criteria: str = "",
        priority: int = 5
    ) -> Optional[TaskStory]:
        """Add a story to an epic."""
        if epic_id not in self.epics:
            return None

        story_id = f"story_{uuid.uuid4().hex[:8]}"
        story = TaskStory(
            id=story_id,
            description=description,
            acceptance_criteria=acceptance_criteria,
            priority=priority,
            parent_epic_id=epic_id,
            status=StoryStatus.READY,  # Stories start as READY
        )
        self.stories[story_id] = story
        self.epics[epic_id].stories.append(story)
        return story

    def get_story(self, story_id: str) -> Optional[TaskStory]:
        return self.stories.get(story_id)

    # ─── Task Operations ──────────────────────────────────────────────────────

    def add_task(
        self,
        story_id: str,
        description: str,
        dependencies: Optional[set[str]] = None,
        priority: int = 5,
        estimated_context_cost: float = 0.0,
        success_criteria: str = ""
    ) -> Optional[TaskNode]:
        """Add a task to a story."""
        if story_id not in self.stories:
            return None

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = TaskNode(
            id=task_id,
            description=description,
            dependencies=dependencies or set(),
            priority=priority,
            estimated_context_cost=estimated_context_cost,
            success_criteria=success_criteria,
            parent_story_id=story_id,
        )
        self.tasks[task_id] = task
        self.stories[story_id].tasks.append(task)
        return task

    def get_task(self, task_id: str) -> Optional[TaskNode]:
        return self.tasks.get(task_id)

    def add_tasks_batch(self, story_id: str, tasks: list[dict]) -> list[TaskNode]:
        """Add multiple tasks at once with dependency resolution."""
        added = []
        for task_spec in tasks:
            task = self.add_task(
                story_id=story_id,
                description=task_spec.get("description", ""),
                dependencies=task_spec.get("dependencies"),
                priority=task_spec.get("priority", 5),
                estimated_context_cost=task_spec.get("estimated_context_cost", 0.0),
                success_criteria=task_spec.get("success_criteria", ""),
            )
            if task:
                added.append(task)
        return added

    # ─── Claiming Operations ──────────────────────────────────────────────────

    def claim_next_available(self, agent_id: str) -> TaskClaimResult:
        """Autonomous task claiming across all stories.

        Finds the highest-priority ready task across all stories
        and claims it for the agent. Thread-safe.
        """
        with self._claim_lock:
            self._bump_starvation()

            # Collect all ready tasks from all stories
            ready_tasks: list[tuple[TaskNode, float]] = []
            for story in self.stories.values():
                if story.status not in {StoryStatus.READY, StoryStatus.IN_PROGRESS}:
                    continue

                for task in story.tasks:
                    if self._can_claim(task):
                        effective_priority = task.compute_effective_priority()
                        # Adjust for story priority
                        effective_priority += (story.priority - 5) * 0.2
                        # Adjust for epic priority
                        if task.parent_story_id:
                            story_obj = self.stories.get(task.parent_story_id)
                            if story_obj and story_obj.parent_epic_id:
                                epic = self.epics.get(story_obj.parent_epic_id)
                                if epic:
                                    effective_priority += (epic.priority - 5) * 0.1
                        ready_tasks.append((task, effective_priority))

            # Sort by effective priority descending
            ready_tasks.sort(key=lambda x: x[1], reverse=True)

            if ready_tasks:
                task = ready_tasks[0][0]
                task.claim(agent_id)
                self._update_story_status(task.parent_story_id)
                self._fire_callbacks("on_claim", task)
                return TaskClaimResult(
                    success=True,
                    task=task,
                    reason=f"Claimed by {agent_id} (priority: {ready_tasks[0][1]:.1f})"
                )

            return TaskClaimResult(
                success=False,
                task=None,
                reason="No tasks available for claiming"
            )

    def _can_claim(self, task: TaskNode) -> bool:
        """Check if a task can be claimed."""
        if task.status != TaskStatus.PENDING:
            return False

        for dep_id in task.all_dependencies:
            dep = self.tasks.get(dep_id)
            if dep is None:
                continue
            if dep.status == TaskStatus.FAILED:
                return False
            if dep.status != TaskStatus.COMPLETED:
                return False

        return True

    def _bump_starvation(self) -> None:
        """Increment starvation for unclaimed ready tasks."""
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING and self._can_claim(task):
                task.starvation_level += 1

    # ─── Completion Operations ────────────────────────────────────────────────

    def complete_task(self, task_id: str, result: Optional[dict] = None) -> bool:
        """Mark a task as completed."""
        task = self.tasks.get(task_id)
        if not task:
            return False

        task.complete(result)
        self._update_story_status(task.parent_story_id)
        self._update_epic_status_for_story(task.parent_story_id)
        self._check_unblocked()
        self._fire_callbacks("on_complete", task)

        # Check if story completed
        if task.parent_story_id:
            story = self.stories.get(task.parent_story_id)
            if story and story.status == StoryStatus.COMPLETED:
                self._fire_callbacks("on_story_complete", story)

        return True

    def fail_task(self, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        task = self.tasks.get(task_id)
        if not task:
            return False

        task.fail(error)
        self._update_story_status(task.parent_story_id)
        self._check_unblocked()
        self._fire_callbacks("on_fail", task)
        return True

    def _update_story_status(self, story_id: Optional[str]) -> None:
        """Update story status after task change."""
        if story_id:
            story = self.stories.get(story_id)
            if story:
                story.update_status()

    def _update_epic_status_for_story(self, story_id: Optional[str]) -> None:
        """Update epic status after story change."""
        if story_id:
            story = self.stories.get(story_id)
            if story and story.parent_epic_id:
                epic = self.epics.get(story.parent_epic_id)
                if epic:
                    epic.update_status()
                    if epic.status == EpicStatus.COMPLETED:
                        self._fire_callbacks("on_epic_complete", epic)

    def _check_unblocked(self) -> None:
        """Check for tasks that were blocked but are now unblocked."""
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING and self._can_claim(task):
                self._fire_callbacks("on_unblock", task)

    # ─── Callback Registration ────────────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """Register a callback for forest events.

        Events: on_claim, on_complete, on_fail, on_unblock,
                on_story_complete, on_epic_complete
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _fire_callbacks(self, event: str, obj) -> None:
        """Fire all callbacks for an event."""
        for cb in self._callbacks.get(event, []):
            try:
                cb(obj)
            except Exception:
                pass

    # ─── Query Operations ─────────────────────────────────────────────────────

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get all tasks ready to execute."""
        return [t for t in self.tasks.values() if self._can_claim(t)]

    def get_ready_tasks_for_story(self, story_id: str) -> list[TaskNode]:
        """Get ready tasks for a specific story."""
        story = self.stories.get(story_id)
        if not story:
            return []
        return story.get_ready_tasks()

    def get_in_progress_epics(self) -> list[TaskEpic]:
        """Get all epics currently in progress."""
        return [e for e in self.epics.values() if e.status == EpicStatus.IN_PROGRESS]

    def get_in_progress_stories(self) -> list[TaskStory]:
        """Get all stories currently in progress."""
        return [s for s in self.stories.values() if s.status == StoryStatus.IN_PROGRESS]

    def get_stats(self) -> dict:
        """Get forest statistics."""
        return {
            "epics": {
                "total": len(self.epics),
                "in_progress": len([e for e in self.epics.values() if e.status == EpicStatus.IN_PROGRESS]),
                "completed": len([e for e in self.epics.values() if e.status == EpicStatus.COMPLETED]),
            },
            "stories": {
                "total": len(self.stories),
                "in_progress": len([s for s in self.stories.values() if s.status == StoryStatus.IN_PROGRESS]),
                "completed": len([s for s in self.stories.values() if s.status == StoryStatus.COMPLETED]),
            },
            "tasks": {
                "total": len(self.tasks),
                "pending": len([t for t in self.tasks.values() if t.status == TaskStatus.PENDING]),
                "claimed": len([t for t in self.tasks.values() if t.status == TaskStatus.CLAIMED]),
                "completed": len([t for t in self.tasks.values() if t.status == TaskStatus.COMPLETED]),
                "failed": len([t for t in self.tasks.values() if t.status == TaskStatus.FAILED]),
            },
        }

    def summary(self) -> str:
        """Get a human-readable forest summary."""
        lines = ["TaskForest Status:"]

        lines.append(f"\n  Epics: {len(self.epics)} total")
        for epic in self.epics.values():
            lines.append(f"    {epic.to_summary()}")

        lines.append(f"\n  Stories: {len(self.stories)} total")
        for story in self.stories.values():
            if story.status in {StoryStatus.IN_PROGRESS, StoryStatus.READY}:
                lines.append(f"    {story.to_summary()}")

        lines.append(f"\n  Tasks: {len(self.tasks)} total")
        ready = len(self.get_ready_tasks())
        claimed = len([t for t in self.tasks.values() if t.status == TaskStatus.CLAIMED])
        lines.append(f"    Ready: {ready}, Claimed: {claimed}")

        return "\n".join(lines)