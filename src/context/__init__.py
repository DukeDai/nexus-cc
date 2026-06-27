"""Context management for Nexus — budget monitoring, CLAUDE.md hierarchy, worktree."""

from .monitor import ContextBudgetMonitor, BudgetTier
from .claudemd import ClaudeMD, ClaudeMDLoader
from .worktree import WorktreeManager
from .wal import WALManager
from .checkpoint import CheckpointManager
from .working_buffer import WorkingBuffer, BufferInfo

# Alias for backwards compatibility
ContextMonitor = ContextBudgetMonitor

__all__ = [
    # Budget monitoring
    "ContextBudgetMonitor",
    "ContextMonitor",
    "BudgetTier",
    # CLAUDE.md
    "ClaudeMD",
    "ClaudeMDLoader",
    # Git worktree
    "WorktreeManager",
    # WAL protocol
    "WALManager",
    # Checkpoint
    "CheckpointManager",
    # Working buffer
    "WorkingBuffer",
    "BufferInfo",
]
