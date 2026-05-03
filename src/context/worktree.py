"""Git Worktree Management for Nexus.

Provides utilities for listing, creating, removing, and switching
between git worktrees in a repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""
    path: Path
    branch: Optional[str]
    head: str  # commit SHA or ref
    is_bare: bool = False

    @property
    def is_current(self) -> bool:
        """Check if this is the current worktree (indicated by HEAD)."""
        return self.head.startswith("HEAD")

    @property
    def commit_short(self) -> str:
        """Short commit SHA (first 7 chars)."""
        if len(self.head) >= 7:
            return self.head[:7]
        return self.head


class WorktreeManager:
    """Manages git worktrees for a repository.

    Provides operations for listing, creating, removing, and switching
    between worktrees. Uses 'git worktree' command internally.
    """

    def __init__(self, repo_path: Optional[Path] = None):
        """Initialize worktree manager.

        Args:
            repo_path: Path to git repository. Defaults to cwd.
        """
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()

    def _run_git(self, args: list[str], capture_output: bool = True) -> subprocess.CompletedProcess:
        """Run a git command.

        Args:
            args: Git command arguments.
            capture_output: Whether to capture stdout/stderr.

        Returns:
            CompletedProcess instance.

        Raises:
            subprocess.CalledProcessError: If command fails.
        """
        cmd = ["git", "-C", str(self.repo_path)] + args
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=True,
        )

    def list(self) -> list[WorktreeInfo]:
        """List all worktrees.

        Returns:
            List of WorktreeInfo objects including the main worktree.
        """
        try:
            result = self._run_git(["worktree", "list", "--porcelain"])
        except subprocess.CalledProcessError as e:
            raise WorktreeError(f"Failed to list worktrees: {e.stderr}") from e

        worktrees: list[WorktreeInfo] = []
        current_lines = result.stdout.strip().split("\n") if result.stdout.strip() else []

        i = 0
        while i < len(current_lines):
            line = current_lines[i].strip()

            if line.startswith("worktree "):
                path = Path(line[9:])
                branch = None
                head = ""
                is_bare = False

                # Read subsequent lines for this worktree
                i += 1
                while i < len(current_lines):
                    next_line = current_lines[i].strip()
                    if not next_line:
                        break
                    if next_line.startswith("worktree "):
                        break
                    if next_line.startswith("branch "):
                        branch = next_line[8:]
                    elif next_line.startswith("HEAD "):
                        head = next_line[5:]
                    elif next_line == "bare":
                        is_bare = True
                    i += 1

                worktrees.append(WorktreeInfo(
                    path=path,
                    branch=branch,
                    head=head,
                    is_bare=is_bare,
                ))
            else:
                i += 1

        return worktrees

    def create(
        self,
        path: Path,
        branch: Optional[str] = None,
        create_branch: bool = False,
        start_point: Optional[str] = None,
    ) -> WorktreeInfo:
        """Create a new worktree.

        Args:
            path: Directory for the new worktree.
            branch: Branch name for the worktree. If None, uses HEAD.
            create_branch: If True, create a new branch named after `branch`.
            start_point: Starting point for new branch (commit/branch/ref).

        Returns:
            WorktreeInfo for the newly created worktree.

        Raises:
            WorktreeError: If creation fails.
        """
        if path.exists():
            raise WorktreeError(f"Path already exists: {path}")

        args = ["worktree", "add"]

        if create_branch:
            if not branch:
                raise WorktreeError("Branch name required when create_branch=True")
            args.extend(["-b", branch])
        elif branch:
            args.extend(["-b", branch])  # Checkout existing branch

        args.append(str(path))

        if start_point:
            args.append(start_point)

        try:
            self._run_git(args)
        except subprocess.CalledProcessError as e:
            raise WorktreeError(f"Failed to create worktree: {e.stderr}") from e

        # Find the new worktree in list
        worktrees = self.list()
        for wt in worktrees:
            if wt.path.resolve() == path.resolve():
                return wt

        raise WorktreeError("Worktree created but not found in list")

    def remove(self, path: Path, force: bool = False) -> None:
        """Remove a worktree.

        Args:
            path: Path to the worktree to remove.
            force: If True, remove even if working tree is dirty.

        Raises:
            WorktreeError: If removal fails.
        """
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")

        try:
            self._run_git(args)
        except subprocess.CalledProcessError as e:
            raise WorktreeError(f"Failed to remove worktree: {e.stderr}") from e

    def prune(self) -> int:
        """Prune stale worktree entries.

        Returns:
            Number of worktrees pruned.

        Raises:
            WorktreeError: If prune fails.
        """
        try:
            result = self._run_git(["worktree", "prune"])
            return 0  # git worktree prune has no output on success
        except subprocess.CalledProcessError as e:
            raise WorktreeError(f"Failed to prune worktrees: {e.stderr}") from e

    def switch(self, branch: str) -> WorktreeInfo:
        """Switch to a different worktree by branch name.

        This is a convenience method that finds a worktree with the
        given branch and returns its info.

        Args:
            branch: Branch name to search for.

        Returns:
            WorktreeInfo for the worktree with the specified branch.

        Raises:
            WorktreeError: If no worktree found with that branch.
        """
        worktrees = self.list()
        for wt in worktrees:
            if wt.branch == branch or branch in str(wt.path):
                return wt

        raise WorktreeError(f"No worktree found for branch: {branch}")

    def find_worktree_for_branch(self, branch: str) -> Optional[WorktreeInfo]:
        """Find worktree associated with a branch.

        Args:
            branch: Branch name to search for.

        Returns:
            WorktreeInfo if found, None otherwise.
        """
        worktrees = self.list()
        for wt in worktrees:
            if wt.branch == branch:
                return wt
        return None

    def get_current_branch(self) -> Optional[str]:
        """Get the current branch name.

        Returns:
            Current branch name or None if detached HEAD.
        """
        try:
            result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            head = result.stdout.strip()
            if head == "HEAD":
                return None
            return head
        except subprocess.CalledProcessError:
            return None

    def get_main_worktree(self) -> Optional[WorktreeInfo]:
        """Get the main worktree (repository root).

        Returns:
            WorktreeInfo for main worktree or None if not found.
        """
        try:
            result = self._run_git(["rev-parse", "--show-toplevel"])
            main_path = Path(result.stdout.strip())
            worktrees = self.list()
            for wt in worktrees:
                if wt.path.resolve() == main_path.resolve():
                    return wt
            return None
        except subprocess.CalledProcessError:
            return None


class WorktreeError(Exception):
    """Exception raised for worktree operations."""
    pass


# Convenience functions
def list_worktrees(repo_path: Optional[Path] = None) -> list[WorktreeInfo]:
    """List all worktrees in a repository."""
    manager = WorktreeManager(repo_path)
    return manager.list()


def create_worktree(
    path: Path,
    branch: Optional[str] = None,
    repo_path: Optional[Path] = None,
    **kwargs: Any,
) -> WorktreeInfo:
    """Create a new worktree."""
    manager = WorktreeManager(repo_path)
    return manager.create(path, branch, **kwargs)


def remove_worktree(
    path: Path,
    repo_path: Optional[Path] = None,
    force: bool = False,
) -> None:
    """Remove a worktree."""
    manager = WorktreeManager(repo_path)
    manager.remove(path, force=force)
