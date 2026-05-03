"""SQLite-based session store for Nexus."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from session.models import SessionMetadata, SessionStatus


class SessionStore:
    """SQLite-backed session persistence layer.

    Manages session lifecycle: create, update, list, load, and delete.
    Uses a SQLite database for durability and fast querying.

    File location: ~/.nexus/sessions/sessions.db

    Schema:
        sessions: id, session_id, project_path, description, status,
                 created_at, updated_at, completed_at, model,
                 context_tier_start, context_tier_end,
                 total_iterations, total_retries, total_escalations,
                 task_count, tasks_completed, tags (JSON), data (JSON)

    Attributes:
        db_path: Path to the SQLite database file.
        timeout: SQLite connection timeout in seconds.
    """

    _TABLE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      TEXT UNIQUE NOT NULL,
        project_path    TEXT NOT NULL,
        description     TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        completed_at    TEXT,
        model           TEXT,
        context_tier_start TEXT NOT NULL DEFAULT 'PEAK',
        context_tier_end   TEXT NOT NULL DEFAULT 'PEAK',
        total_iterations  INTEGER NOT NULL DEFAULT 0,
        total_retries     INTEGER NOT NULL DEFAULT 0,
        total_escalations INTEGER NOT NULL DEFAULT 0,
        task_count         INTEGER NOT NULL DEFAULT 0,
        tasks_completed    INTEGER NOT NULL DEFAULT 0,
        tags              TEXT NOT NULL DEFAULT '[]',
        data             TEXT NOT NULL DEFAULT '{}'
    )
    """

    _INDEX_SCHEMA = """
    CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
    CREATE INDEX IF NOT EXISTS idx_sessions_project_path ON sessions(project_path);
    """

    def __init__(self, db_path: Optional[Path] = None, timeout: float = 5.0):
        """Initialize SessionStore.

        Args:
            db_path: Path to SQLite DB. Defaults to ~/.nexus/sessions/sessions.db.
            timeout: SQLite connection timeout in seconds.
        """
        if db_path is None:
            db_path = Path.home() / ".nexus" / "sessions" / "sessions.db"
        self.db_path = db_path
        self.timeout = timeout
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Ensure the database and tables exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=self.timeout)
        try:
            conn.executescript(self._TABLE_SCHEMA)
            conn.executescript(self._INDEX_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        """Get a new database connection."""
        return sqlite3.connect(str(self.db_path), timeout=self.timeout)

    # -------------------------------------------------------------------------
    # CRUD operations
    # -------------------------------------------------------------------------

    def create(self, metadata: SessionMetadata, data_json: str) -> None:
        """Create a new session record.

        Args:
            metadata: SessionMetadata for the new session.
            data_json: Full SessionData serialized as JSON.

        Raises:
            sqlite3.IntegrityError: If session_id already exists.
        """
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, project_path, description, status,
                    created_at, updated_at, completed_at, model,
                    context_tier_start, context_tier_end,
                    total_iterations, total_retries, total_escalations,
                    task_count, tasks_completed, tags, data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata.session_id,
                    metadata.project_path,
                    metadata.description,
                    metadata.status.value,
                    metadata.created_at,
                    metadata.updated_at,
                    metadata.completed_at,
                    metadata.model,
                    metadata.context_tier_start,
                    metadata.context_tier_end,
                    metadata.total_iterations,
                    metadata.total_retries,
                    metadata.total_escalations,
                    metadata.task_count,
                    metadata.tasks_completed,
                    json.dumps(metadata.tags),
                    data_json,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def update(
        self,
        session_id: str,
        metadata: SessionMetadata,
        data_json: str,
    ) -> bool:
        """Update an existing session.

        Args:
            session_id: ID of the session to update.
            metadata: Updated SessionMetadata.
            data_json: Updated SessionData JSON.

        Returns:
            True if a row was updated, False if not found.
        """
        conn = self._conn()
        try:
            cursor = conn.execute(
                """
                UPDATE sessions SET
                    project_path=?, description=?, status=?,
                    updated_at=?, completed_at=?, model=?,
                    context_tier_start=?, context_tier_end=?,
                    total_iterations=?, total_retries=?, total_escalations=?,
                    task_count=?, tasks_completed=?, tags=?, data=?
                WHERE session_id=?
                """,
                (
                    metadata.project_path,
                    metadata.description,
                    metadata.status.value,
                    metadata.updated_at,
                    metadata.completed_at,
                    metadata.model,
                    metadata.context_tier_start,
                    metadata.context_tier_end,
                    metadata.total_iterations,
                    metadata.total_retries,
                    metadata.total_escalations,
                    metadata.task_count,
                    metadata.tasks_completed,
                    json.dumps(metadata.tags),
                    data_json,
                    session_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get(self, session_id: str) -> Optional[tuple[SessionMetadata, str]]:
        """Load a session by ID.

        Args:
            session_id: ID of the session to load.

        Returns:
            (SessionMetadata, data_json) if found, None otherwise.
        """
        conn = self._conn()
        try:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_metadata_and_data(row)
        finally:
            conn.close()

    def list_sessions(
        self,
        project_path: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SessionMetadata]:
        """List sessions with optional filters.

        Args:
            project_path: Filter by project path (prefix match).
            status: Filter by session status.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of matching SessionMetadata objects, newest first.
        """
        conn = self._conn()
        try:
            query = "SELECT * FROM sessions"
            conditions = []
            params: list[Any] = []

            if project_path is not None:
                conditions.append("project_path LIKE ?")
                params.append(f"{project_path}%")
            if status is not None:
                conditions.append("status = ?")
                params.append(status.value)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_metadata(row) for row in rows]
        finally:
            conn.close()

    def delete(self, session_id: str) -> bool:
        """Delete a session by ID.

        Args:
            session_id: ID of the session to delete.

        Returns:
            True if a row was deleted, False if not found.
        """
        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id=?",
                (session_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics across all sessions.

        Returns:
            Dict with total_sessions, active, completed, avg_tasks, etc.
        """
        conn = self._conn()
        try:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                    AVG(task_count) as avg_tasks,
                    AVG(total_iterations) as avg_iterations,
                    SUM(total_iterations) as sum_iterations
                FROM sessions
                """
            )
            row = cursor.fetchone()
            return {
                "total_sessions": row[0] or 0,
                "active": row[1] or 0,
                "completed": row[2] or 0,
                "failed": row[3] or 0,
                "avg_tasks": round(row[4] or 0, 2),
                "avg_iterations": round(row[5] or 0, 2),
                "sum_iterations": row[6] or 0,
            }
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _row_to_metadata(self, row: sqlite3.Row) -> SessionMetadata:
        """Convert a DB row to SessionMetadata."""
        return SessionMetadata(
            session_id=row["session_id"],
            project_path=row["project_path"],
            description=row["description"],
            status=SessionStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            model=row["model"],
            context_tier_start=row["context_tier_start"],
            context_tier_end=row["context_tier_end"],
            total_iterations=row["total_iterations"],
            total_retries=row["total_retries"],
            total_escalations=row["total_escalations"],
            task_count=row["task_count"],
            tasks_completed=row["tasks_completed"],
            tags=json.loads(row["tags"]),
        )

    def _row_to_metadata_and_data(
        self, row: sqlite3.Row
    ) -> tuple[SessionMetadata, str]:
        """Convert a DB row to (SessionMetadata, data_json)."""
        return self._row_to_metadata(row), row["data"]
