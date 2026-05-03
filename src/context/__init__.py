"""Context management for Nexus — budget monitoring, CLAUDE.md hierarchy, worktree."""

from .monitor import ContextBudgetMonitor, BudgetTier
from .claudemd import ClaudeMD, ClaudeMDLoader
from .worktree import WorktreeManager

# Alias for backwards compatibility
ContextMonitor = ContextBudgetMonitor

__all__ = [
    "ContextBudgetMonitor",
    "ContextMonitor",
    "BudgetTier",
    "ClaudeMD",
    "ClaudeMDLoader",
    "WorktreeManager",
]
