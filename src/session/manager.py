"""Session manager — orchestrates save, restore, and resume for Nexus sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..ralphloop.orchestrator import RalphLoop
from ..ralphloop.states import RalphState
from ..session.models import (
    AgentStateRecord,
    RalphLoopSnapshot,
    SessionData,
    SessionMetadata,
    SessionStatus,
    TaskRecord,
    new_session_id,
)
from ..session.store import SessionStore


class SessionManager:
    """Manages Nexus session lifecycle: save, restore, list, resume.

    Coordinates with RalphLoop, SessionStore, and Checkpoint to provide
    full session persistence and resumption across CLI invocations.

    Example:
        # Save current session
        manager = SessionManager(project_path="/my/project")
        session_id = manager.create(project_desc="Add auth feature")
        manager.save(session_id, ralphloop, agent_states, context_usage)

        # Resume later
        data = manager.load(session_id)
        orchestrator = manager.restore(data)

    Attributes:
        store: SessionStore instance for SQLite persistence.
        checkpoint_dir: Directory for RalphLoop checkpoint files.
        on_save: Optional callback called after each save.
    """

    def __init__(
        self,
        project_path: Optional[str] = None,
        store: Optional[SessionStore] = None,
        checkpoint_dir: Optional[Path] = None,
        on_save: Optional[Callable[[str], None]] = None,
    ):
        """Initialize SessionManager.

        Args:
            project_path: Root path of the project. Defaults to cwd.
            store: SessionStore instance. Defaults to a new one.
            checkpoint_dir: Directory for RalphLoop checkpoints.
            on_save: Optional callback(session_id) after each save.
        """
        self.project_path = Path(project_path or Path.cwd()).resolve()
        self.store = store or SessionStore()
        self.checkpoint_dir = checkpoint_dir
        self.on_save = on_save

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    def create(
        self,
        description: str,
        model: Optional[str] = None,
        tags: Optional[list[str]] = None,
        initial_task_queue: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """Create a new session.

        Args:
            description: Human-readable description of the session goal.
            model: Model name used for this session.
            tags: Optional tags for categorization.
            initial_task_queue: Optional initial task queue.

        Returns:
            The new session_id string.
        """
        session_id = new_session_id()
        metadata = SessionMetadata(
            session_id=session_id,
            project_path=str(self.project_path),
            description=description,
            model=model,
            tags=tags or [],
            task_count=len(initial_task_queue) if initial_task_queue else 0,
        )
        session_data = SessionData(
            metadata=metadata,
            ralphloop=RalphLoopSnapshot(
                state=RalphState.PLAN.name,
                task_index=0,
                retry_count=0,
                error_log=[],
                metrics={},
            ),
            task_queue=initial_task_queue or [],
        )
        self.store.create(metadata, session_data.to_json())
        return session_id

    def save(
        self,
        session_id: str,
        ralphloop: RalphLoop,
        agent_states: Optional[list[AgentStateRecord]] = None,
        context_usage: float = 0.0,
        pending_hooks: Optional[list[str]] = None,
        worktree_branch: Optional[str] = None,
    ) -> bool:
        """Save current session state to the store.

        Args:
            session_id: ID of the session to save.
            ralphloop: Current RalphLoop instance to snapshot.
            agent_states: Current states of all agents.
            context_usage: Current context usage percentage.
            pending_hooks: Hooks waiting to be processed.
            worktree_branch: Current git worktree branch.

        Returns:
            True if saved successfully, False if session not found.
        """
        result = self.store.get(session_id)
        if result is None:
            return False

        metadata, _ = result
        metadata.updated_at = datetime.now().isoformat()
        metadata.total_iterations = ralphloop.metrics.total_iterations
        metadata.total_retries = ralphloop.metrics.total_retries
        metadata.total_escalations = ralphloop.metrics.total_escalations

        if ralphloop.metrics.states_visited:
            metadata.context_tier_end = str(ralphloop.context_tier.name)

        pending = metadata.task_count - metadata.tasks_completed
        if ralphloop.state in (RalphState.COMMIT, RalphState.ABORT):
            metadata.status = (
                SessionStatus.COMPLETED
                if ralphloop.state == RalphState.COMMIT
                else SessionStatus.FAILED
            )
            metadata.completed_at = datetime.now().isoformat()

        session_data = SessionData(
            metadata=metadata,
            ralphloop=RalphLoopSnapshot(
                state=ralphloop.state.name,
                task_index=ralphloop.task_index,
                retry_count=ralphloop.retry_count,
                error_log=ralphloop.error_log[-20:],
                metrics={
                    "total_iterations": ralphloop.metrics.total_iterations,
                    "total_retries": ralphloop.metrics.total_retries,
                    "total_escalations": ralphloop.metrics.total_escalations,
                    "duration_seconds": ralphloop.metrics.duration_seconds(),
                },
            ),
            task_queue=ralphloop.task_queue,
            agent_states=agent_states or [],
            context_usage_at_checkpoint=context_usage,
            pending_hooks=pending_hooks or [],
            worktree_branch=worktree_branch,
        )

        ok = self.store.update(session_id, metadata, session_data.to_json())
        if ok and self.on_save:
            self.on_save(session_id)
        return ok

    def load(self, session_id: str) -> Optional[SessionData]:
        """Load session data from the store.

        Args:
            session_id: ID of the session to load.

        Returns:
            SessionData if found, None otherwise.
        """
        result = self.store.get(session_id)
        if result is None:
            return None
        _, data_json = result
        from ..session.models import SessionData as SD
        return SD.from_dict(__import__("json").loads(data_json))

    def restore(
        self,
        session_data: SessionData,
        context_monitor: Optional[Callable[[], float]] = None,
    ) -> RalphLoop:
        """Restore a RalphLoop from session data.

        Args:
            session_data: SessionData loaded from the store.
            context_monitor: Callable returning current context usage.
                           Defaults to lambda returning context_usage_at_checkpoint.

        Returns:
            A new RalphLoop instance in the saved state, ready to run.
        """
        context_monitor = context_monitor or (
            lambda: session_data.context_usage_at_checkpoint
        )
        ralf = RalphLoop(
            task_queue=session_data.task_queue,
            context_monitor=context_monitor,
            checkpoint_dir=self.checkpoint_dir,
        )
        ralf.state = RalphState[session_data.ralphloop.state]
        ralf.task_index = session_data.ralphloop.task_index
        ralf.retry_count = session_data.ralphloop.retry_count
        ralf.error_log = list(session_data.ralphloop.error_log)
        return ralf

    def list(
        self,
        status: Optional[SessionStatus] = None,
        limit: int = 20,
    ) -> list[SessionMetadata]:
        """List sessions for the current project.

        Args:
            status: Optional status filter.
            limit: Maximum results to return.

        Returns:
            List of SessionMetadata, newest first.
        """
        return self.store.list_sessions(
            project_path=str(self.project_path),
            status=status,
            limit=limit,
        )

    def delete(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: ID of the session to delete.

        Returns:
            True if deleted, False if not found.
        """
        return self.store.delete(session_id)

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate session statistics.

        Returns:
            Dict with total_sessions, active, completed, failed, avg_tasks, etc.
        """
        return self.store.get_stats()
