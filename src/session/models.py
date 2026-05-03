"""Session data models for Nexus session persistence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class SessionStatus(Enum):
    """Session lifecycle status."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class TaskStatus(Enum):
    """Task-level status within a session."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class TaskRecord:
    """Record of a single task within a session."""
    task_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    retry_count: int = 0
    error: Optional[str] = None
    result_summary: Optional[str] = None


@dataclass
class AgentStateRecord:
    """Snapshot of an agent's state at a point in time."""
    agent_type: str
    state: str
    current_task: Optional[str] = None
    confidence: float = 1.0
    errors: list[str] = field(default_factory=list)


@dataclass
class SessionMetadata:
    """Top-level session metadata.

    Attributes:
        session_id: Unique identifier for this session.
        project_path: Root of the project this session operates on.
        description: Human-readable description of session goal.
        status: Current lifecycle status.
        created_at: ISO timestamp of session creation.
        updated_at: ISO timestamp of last modification.
        completed_at: ISO timestamp when session ended (if applicable).
        model: Model used for this session.
        context_tier_start: Initial context budget tier.
        context_tier_end: Final context budget tier.
        total_iterations: RalphLoop iterations executed.
        total_retries: Total retry count across all tasks.
        total_escalations: Number of escalation events.
        task_count: Total number of tasks in this session.
        tasks_completed: Number of successfully completed tasks.
        tags: Arbitrary tags for session categorization.
    """
    session_id: str
    project_path: str
    description: str
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    model: Optional[str] = None
    context_tier_start: str = "PEAK"
    context_tier_end: str = "PEAK"
    total_iterations: int = 0
    total_retries: int = 0
    total_escalations: int = 0
    task_count: int = 0
    tasks_completed: int = 0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_path": self.project_path,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "model": self.model,
            "context_tier_start": self.context_tier_start,
            "context_tier_end": self.context_tier_end,
            "total_iterations": self.total_iterations,
            "total_retries": self.total_retries,
            "total_escalations": self.total_escalations,
            "task_count": self.task_count,
            "tasks_completed": self.tasks_completed,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionMetadata:
        d = dict(d)
        d["status"] = SessionStatus(d.get("status", "active"))
        return cls(**d)


@dataclass
class RalphLoopSnapshot:
    """Snapshot of RalphLoop state for session restoration.

    Attributes:
        state: RalphLoop state enum name string.
        task_index: Index into the task queue.
        retry_count: Current retry count.
        error_log: Recent error messages.
        metrics: Serialized RalphLoopMetrics.
    """
    state: str
    task_index: int
    retry_count: int
    error_log: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionData:
    """Complete session data for persistence.

    Contains all information needed to fully restore a Nexus session
    including RalphLoop state, task queue, agent states, and metadata.
    """
    metadata: SessionMetadata
    ralphloop: RalphLoopSnapshot
    task_queue: list[dict[str, Any]]
    agent_states: list[AgentStateRecord] = field(default_factory=list)
    context_usage_at_checkpoint: float = 0.0
    pending_hooks: list[str] = field(default_factory=list)
    worktree_branch: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "ralphloop": {
                "state": self.ralphloop.state,
                "task_index": self.ralphloop.task_index,
                "retry_count": self.ralphloop.retry_count,
                "error_log": self.ralphloop.error_log,
                "metrics": self.ralphloop.metrics,
            },
            "task_queue": self.task_queue,
            "agent_states": [
                {
                    "agent_type": a.agent_type,
                    "state": a.state,
                    "current_task": a.current_task,
                    "confidence": a.confidence,
                    "errors": a.errors,
                }
                for a in self.agent_states
            ],
            "context_usage_at_checkpoint": self.context_usage_at_checkpoint,
            "pending_hooks": self.pending_hooks,
            "worktree_branch": self.worktree_branch,
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionData:
        meta = SessionMetadata.from_dict(d["metadata"])
        rl = d["ralphloop"]
        ralphloop = RalphLoopSnapshot(
            state=rl["state"],
            task_index=rl["task_index"],
            retry_count=rl["retry_count"],
            error_log=rl.get("error_log", []),
            metrics=rl.get("metrics", {}),
        )
        agent_states = [
            AgentStateRecord(
                agent_type=a["agent_type"],
                state=a["state"],
                current_task=a.get("current_task"),
                confidence=a.get("confidence", 1.0),
                errors=a.get("errors", []),
            )
            for a in d.get("agent_states", [])
        ]
        return cls(
            metadata=meta,
            ralphloop=ralphloop,
            task_queue=d.get("task_queue", []),
            agent_states=agent_states,
            context_usage_at_checkpoint=d.get("context_usage_at_checkpoint", 0.0),
            pending_hooks=d.get("pending_hooks", []),
            worktree_branch=d.get("worktree_branch"),
        )


def new_session_id() -> str:
    """Generate a new unique session ID."""
    return str(uuid.uuid4())[:8]
