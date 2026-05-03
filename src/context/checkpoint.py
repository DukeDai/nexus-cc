"""Checkpoint system for saving and restoring agent state."""

import json
import time
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
