"""Checkpoint system for saving and restoring agent state."""

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class CheckpointData:
    """Container for checkpoint state."""

    # Core state
    task_queue: list[str] = field(default_factory=list)
    completed_tasks: list[str] = field(default_factory=list)
    retry_count: int = 0

    # Monitor snapshot
    monitor_tokens: int = 0
    monitor_max_tokens: int = 200_000

    # Metadata
    timestamp: float = field(default_factory=time.time)
    checkpoint_id: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointData":
        """Create instance from dictionary."""
        return cls(**data)


class Checkpoint:
    """Save and restore agent state for recovery."""

    def __init__(self, checkpoint_dir: str | Path = "./checkpoints") -> None:
        """
        Initialize checkpoint manager.

        Args:
            checkpoint_dir: Directory to store checkpoint files.
        """
        self._checkpoint_dir = Path(checkpoint_dir)

    def save(
        self,
        task_queue: list[str],
        completed_tasks: list[str],
        retry_count: int,
        monitor_tokens: int,
        monitor_max_tokens: int,
        checkpoint_id: Optional[str] = None,
    ) -> Path:
        """
        Save a checkpoint to disk.

        Args:
            task_queue: Current pending tasks.
            completed_tasks: List of completed task IDs/names.
            retry_count: Current retry count.
            monitor_tokens: Current token usage from monitor.
            monitor_max_tokens: Max context tokens from monitor.
            checkpoint_id: Optional custom ID (defaults to timestamp).

        Returns:
            Path to the saved checkpoint file.
        """
        if checkpoint_id is None:
            checkpoint_id = f"ckpt_{int(time.time() * 1000)}"

        checkpoint_data = CheckpointData(
            task_queue=list(task_queue),
            completed_tasks=list(completed_tasks),
            retry_count=retry_count,
            monitor_tokens=monitor_tokens,
            monitor_max_tokens=monitor_max_tokens,
            checkpoint_id=checkpoint_id,
        )

        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._checkpoint_dir / f"{checkpoint_id}.json"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data.to_dict(), f, indent=2)

        return file_path

    def restore(self, checkpoint_id: str) -> CheckpointData:
        """
        Restore state from a checkpoint.

        Args:
            checkpoint_id: ID of the checkpoint to restore.

        Returns:
            CheckpointData with restored state.

        Raises:
            FileNotFoundError: If checkpoint file doesn't exist.
        """
        file_path = self._checkpoint_dir / f"{checkpoint_id}.json"
        if not file_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return CheckpointData.from_dict(data)

    def restore_latest(self) -> CheckpointData:
        """
        Restore the most recent checkpoint.

        Returns:
            CheckpointData from the latest checkpoint file.

        Raises:
            FileNotFoundError: If no checkpoints exist.
        """
        if not self._checkpoint_dir.exists():
            raise FileNotFoundError("No checkpoints found")

        checkpoint_files = sorted(
            self._checkpoint_dir.glob("ckpt_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not checkpoint_files:
            raise FileNotFoundError("No checkpoints found")

        checkpoint_id = checkpoint_files[0].stem
        return self.restore(checkpoint_id)

    def list_checkpoints(self) -> list[str]:
        """List all available checkpoint IDs."""
        if not self._checkpoint_dir.exists():
            return []
        return sorted([p.stem for p in self._checkpoint_dir.glob("ckpt_*.json")])

    def delete(self, checkpoint_id: str) -> None:
        """Delete a specific checkpoint."""
        file_path = self._checkpoint_dir / f"{checkpoint_id}.json"
        if file_path.exists():
            file_path.unlink()


class CheckpointManager:
    """SQLite-based checkpoint manager for persistent state storage."""

    def __init__(self, db_path: Path | str) -> None:
        """
        Initialize CheckpointManager with SQLite database.

        Args:
            db_path: Path to SQLite database file.
        """
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection, creating if needed."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                state TEXT NOT NULL,
                task_index INTEGER NOT NULL,
                retry_count INTEGER NOT NULL,
                context_usage REAL NOT NULL,
                error_log TEXT NOT NULL,
                task_queue TEXT NOT NULL
            )
            """
        )
        conn.commit()

    def save_checkpoint(
        self,
        state: str,
        task_index: int,
        retry_count: int,
        context_usage: float,
        task_queue: list[dict],
        error_log: list[dict],
    ) -> str:
        """
        Save a checkpoint to the database.

        Args:
            state: Current state (e.g., "PLAN", "EXEC").
            task_index: Current task index.
            retry_count: Number of retries.
            context_usage: Context token usage percentage.
            task_queue: List of task dictionaries.
            error_log: List of error dictionaries.

        Returns:
            The checkpoint ID (UUID string).
        """
        checkpoint_id = str(uuid.uuid4())
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO checkpoints 
            (id, timestamp, state, task_index, retry_count, context_usage, error_log, task_queue)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint_id,
                timestamp,
                state,
                task_index,
                retry_count,
                context_usage,
                json.dumps(error_log),
                json.dumps(task_queue),
            ),
        )
        conn.commit()
        return checkpoint_id

    def load_checkpoint(self, checkpoint_id: str) -> dict | None:
        """
        Load a checkpoint by ID.

        Args:
            checkpoint_id: The checkpoint ID to load.

        Returns:
            Dictionary with checkpoint data or None if not found.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "state": row["state"],
            "task_index": row["task_index"],
            "retry_count": row["retry_count"],
            "context_usage": row["context_usage"],
            "error_log": json.loads(row["error_log"]),
            "task_queue": json.loads(row["task_queue"]),
        }

    def list_checkpoints(self) -> list[dict]:
        """
        List all checkpoints ordered by timestamp descending.

        Returns:
            List of checkpoint dictionaries.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM checkpoints ORDER BY timestamp DESC"
        )
        rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "state": row["state"],
                "task_index": row["task_index"],
                "retry_count": row["retry_count"],
                "context_usage": row["context_usage"],
                "error_log": json.loads(row["error_log"]),
                "task_queue": json.loads(row["task_queue"]),
            }
            for row in rows
        ]

    def delete_checkpoint(self, checkpoint_id: str) -> None:
        """
        Delete a checkpoint by ID.

        Args:
            checkpoint_id: The checkpoint ID to delete.
        """
        conn = self._get_conn()
        conn.execute("DELETE FROM checkpoints WHERE id = ?", (checkpoint_id,))
        conn.commit()
