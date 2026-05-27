"""WAL-Checkpoint Coordinator — Unified Recovery Strategy.

Coordinates WAL (replay-based recovery) and Checkpoint (snapshot-based recovery)
to provide optimal recovery after crashes.

Recovery Strategy:
1. On crash, first try Checkpoint (faster, jumps to known stable state)
2. If Checkpoint unavailable/invalid, fall back to WAL replay
3. WAL replay reconstructs state by replaying tool calls in order
4. After recovery, validate state consistency before resuming

Key principle: Checkpoint provides fast recovery to known good state.
WAL provides fine-grained recovery when Checkpoint is stale or unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from .wal import WALManager, WALEntry
from ..context.checkpoint import Checkpoint, CheckpointData


class RecoveryStrategy(Enum):
    """How to recover from crash."""
    CHECKPOINT_ONLY = auto()   # Use checkpoint, ignore WAL
    WAL_ONLY = auto()          # Replay WAL, ignore checkpoint
    CHECKPOINT_FIRST = auto()  # Try checkpoint, fallback to WAL
    WAL_FIRST = auto()         # Try WAL replay, fallback to checkpoint


@dataclass
class RecoveryContext:
    """Result of a recovery attempt."""
    success: bool
    strategy_used: RecoveryStrategy
    recovered_state: Optional[dict] = None
    entries_replayed: int = 0
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RecoveryPlan:
    """Planned recovery actions."""
    strategy: RecoveryStrategy
    checkpoint_id: Optional[str] = None
    wal_sequence_start: int = 0
    estimated_entries: int = 0
    action_summary: str = ""


class WALCheckpointCoordinator:
    """Coordinates WAL and Checkpoint for unified crash recovery.

    Usage:
        coordinator = WALCheckpointCoordinator(
            wal_dir=Path(".nexus/wal"),
            checkpoint_dir=Path(".nexus/checkpoints"),
        )

        # On crash:
        recovery = coordinator.plan_recovery()
        if recovery.strategy == RecoveryStrategy.CHECKPOINT_FIRST:
            result = coordinator.recover_with_checkpoint(recovery.checkpoint_id)
            if not result.success:
                result = coordinator.recover_with_wal(recovery.wal_sequence_start)

        # On successful phase completion:
        coordinator.record_checkpoint(state="ACT", task_index=5)

        # On successful commit:
        coordinator.clear_wal()  # WAL served its purpose
    """

    # Checkpoint is preferred if less than this many WAL entries since last checkpoint
    CHECKPOINT_PREFERRED_MAX_WAL_ENTRIES = 50

    def __init__(
        self,
        wal_dir: Path | str = Path(".nexus/wal"),
        checkpoint_dir: Path | str = Path(".nexus/checkpoints"),
    ):
        self.wal = WALManager(wal_dir)
        self.checkpoint = Checkpoint(checkpoint_dir)
        self._last_checkpoint_seq: int = 0  # WAL sequence at last checkpoint

    def plan_recovery(self) -> RecoveryPlan:
        """Analyze situation and plan best recovery approach.

        Returns RecoveryPlan with recommended strategy and details.
        """
        # Check for available checkpoints
        checkpoint_list = self.checkpoint.list_checkpoints()
        has_checkpoint = len(checkpoint_list) > 0

        # Check WAL entries
        recovery_plan = self.wal.get_recovery_plan()
        wal_entries = recovery_plan.get("total_entries", 0)
        has_wal = wal_entries > 0

        if not has_checkpoint and not has_wal:
            return RecoveryPlan(
                strategy=RecoveryStrategy.CHECKPOINT_ONLY,
                action_summary="No recovery data available, starting fresh"
            )

        # Get last checkpoint info
        last_ckpt = recovery_plan.get("last_checkpoint")
        if last_ckpt:
            self._last_checkpoint_seq = last_ckpt.get("task_index", 0)

        # Count WAL entries since last checkpoint
        entries_since_checkpoint = wal_entries - self._last_checkpoint_seq

        # Decide strategy
        if has_checkpoint and not has_wal:
            return RecoveryPlan(
                strategy=RecoveryStrategy.CHECKPOINT_ONLY,
                checkpoint_id=checkpoint_list[-1].get("checkpoint_id") if checkpoint_list else None,
                action_summary=f"Using checkpoint recovery (no WAL data)"
            )

        if has_wal and not has_checkpoint:
            return RecoveryPlan(
                strategy=RecoveryStrategy.WAL_ONLY,
                wal_sequence_start=0,
                estimated_entries=wal_entries,
                action_summary=f"WAL replay recovery ({wal_entries} entries)"
            )

        # Both available: decide based on recency
        if entries_since_checkpoint <= self.CHECKPOINT_PREFERRED_MAX_WAL_ENTRIES:
            return RecoveryPlan(
                strategy=RecoveryStrategy.CHECKPOINT_FIRST,
                checkpoint_id=checkpoint_list[-1].get("checkpoint_id") if checkpoint_list else None,
                wal_sequence_start=self._last_checkpoint_seq,
                estimated_entries=entries_since_checkpoint,
                action_summary=(
                    f"Checkpoint preferred ({entries_since_checkpoint} WAL entries since last checkpoint)"
                )
            )
        else:
            return RecoveryPlan(
                strategy=RecoveryStrategy.WAL_FIRST,
                wal_sequence_start=0,
                estimated_entries=wal_entries,
                action_summary=(
                    f"WAL replay preferred (too many entries since checkpoint: {entries_since_checkpoint})"
                )
            )

    def recover_with_checkpoint(
        self,
        checkpoint_id: Optional[str] = None
    ) -> RecoveryContext:
        """Recover using checkpoint (fast, jumps to stable state).

        Args:
            checkpoint_id: Specific checkpoint to restore, or latest if None

        Returns:
            RecoveryContext with recovered state
        """
        try:
            if checkpoint_id:
                ckpt_data = self.checkpoint.restore(checkpoint_id)
            else:
                # Get latest checkpoint
                checkpoints = self.checkpoint.list_checkpoints()
                if not checkpoints:
                    return RecoveryContext(
                        success=False,
                        strategy_used=RecoveryStrategy.CHECKPOINT_ONLY,
                        error="No checkpoints available"
                    )
                ckpt_data = self.checkpoint.restore(
                    checkpoints[-1].get("checkpoint_id", "")
                )

            if ckpt_data is None:
                return RecoveryContext(
                    success=False,
                    strategy_used=RecoveryStrategy.CHECKPOINT_ONLY,
                    error="Checkpoint restore failed"
                )

            return RecoveryContext(
                success=True,
                strategy_used=RecoveryStrategy.CHECKPOINT_ONLY,
                recovered_state=ckpt_data.to_dict() if hasattr(ckpt_data, 'to_dict') else {},
            )

        except Exception as e:
            return RecoveryContext(
                success=False,
                strategy_used=RecoveryStrategy.CHECKPOINT_ONLY,
                error=str(e)
            )

    def recover_with_wal(self, from_sequence: int = 0) -> RecoveryContext:
        """Recover by replaying WAL entries (fine-grained, may be slower).

        Args:
            from_sequence: Start replay from this sequence number (0 = beginning)

        Returns:
            RecoveryContext with replayed state
        """
        try:
            entries = self.wal.recover()

            # Filter to entries after from_sequence
            if from_sequence > 0:
                entries = [e for e in entries if e.sequence > from_sequence]

            # Build replayable state
            state = {
                "transitions": [],
                "tool_calls": [],
                "checkpoint": None,
            }

            for entry in entries:
                if entry.entry_type == "transition":
                    state["transitions"].append(entry.data)
                elif entry.entry_type == "tool_call":
                    state["tool_calls"].append(entry.data)
                elif entry.entry_type == "checkpoint":
                    state["checkpoint"] = entry.data

            return RecoveryContext(
                success=True,
                strategy_used=RecoveryStrategy.WAL_ONLY,
                recovered_state=state,
                entries_replayed=len(entries),
            )

        except Exception as e:
            return RecoveryContext(
                success=False,
                strategy_used=RecoveryStrategy.WAL_ONLY,
                error=str(e)
            )

    def record_checkpoint(
        self,
        state: str,
        task_index: int = 0,
        retry_count: int = 0,
        context_summary: Optional[dict] = None,
    ) -> str:
        """Record a checkpoint and update WAL tracking.

        Call this after successful phase completion to establish
        a recovery point.

        Returns:
            Checkpoint ID
        """
        # Record in Checkpoint (persistent)
        checkpoint_data = CheckpointData(
            task_queue=[],  # Will be filled by caller
            completed_tasks=[],
            retry_count=retry_count,
            monitor_tokens=0,
            monitor_max_tokens=200_000,
            checkpoint_id=f"ckpt_{int(datetime.now().timestamp() * 1000)}",
        )
        path = self.checkpoint.save(
            task_queue=[],
            completed_tasks=[],
            retry_count=retry_count,
            monitor_tokens=0,
            monitor_max_tokens=200_000,
        )

        # Record in WAL (also logs checkpoint entry)
        self.wal.log_checkpoint(
            state=state,
            context_summary=context_summary or {},
            task_index=task_index,
            retry_count=retry_count,
        )

        # Update tracking
        self._last_checkpoint_seq = self.wal._current_seq

        return checkpoint_data.checkpoint_id

    def record_wal_entry(self, entry: WALEntry) -> int:
        """Record a WAL entry and return sequence number."""
        return self.wal._current_seq + 1  # Caller should use actual write

    def clear_wal(self) -> None:
        """Clear WAL after successful commit.

        Call this after successful task completion to clean up
        WAL files (they've served their purpose).
        """
        self.wal.clear()

    def get_recovery_status(self) -> dict:
        """Get current recovery system status."""
        checkpoints = self.checkpoint.list_checkpoints()
        wal_plan = self.wal.get_recovery_plan()

        return {
            "checkpoint_available": len(checkpoints) > 0,
            "checkpoint_count": len(checkpoints),
            "latest_checkpoint": checkpoints[-1] if checkpoints else None,
            "wal_entries": wal_plan.get("total_entries", 0),
            "missing_results": wal_plan.get("missing_results", []),
            "last_checkpoint_seq": self._last_checkpoint_seq,
            "preferred_strategy": self.plan_recovery().strategy.name,
        }