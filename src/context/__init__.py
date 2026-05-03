"""Context management for Nexus — budget monitoring, CLAUDE.md hierarchy, worktree."""

from context.monitor import ContextBudgetMonitor, BudgetTier
from context.claudemd import ClaudeMD, ClaudeMDLoader
from context.worktree import WorktreeManager

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
