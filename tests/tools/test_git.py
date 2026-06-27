"""Tests for GitTool."""
from __future__ import annotations

import subprocess

import pytest

from src.tools.git import GitTool


@pytest.fixture
def git_repo(tmp_path):
    """Initialize a git repo in tmp_path."""
    subprocess.check_call(["git", "init"], cwd=str(tmp_path))
    subprocess.check_call(
        ["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path)
    )
    subprocess.check_call(
        ["git", "config", "user.name", "Test User"], cwd=str(tmp_path)
    )
    return tmp_path


@pytest.mark.asyncio
async def test_git_status_clean(git_repo):
    """Run git status in a fresh repo; expect exit_code 0 and clean output."""
    tool = GitTool(workdir=str(git_repo))
    result = await tool.execute(subcommand="status")
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_git_log_after_commit(git_repo):
    """Add a file, commit, run git log; expect the commit message in output."""
    (git_repo / "README.md").write_text("hello\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=str(git_repo))
    subprocess.check_call(
        ["git", "commit", "-m", "initial commit"], cwd=str(git_repo)
    )

    tool = GitTool(workdir=str(git_repo))
    result = await tool.execute(subcommand="log")
    assert result["exit_code"] == 0
    assert "initial commit" in result["stdout"]


@pytest.mark.asyncio
async def test_git_disallowed_subcommand_raises(git_repo):
    """Attempting a disallowed subcommand (e.g., push) should raise ValueError."""
    tool = GitTool(workdir=str(git_repo))
    with pytest.raises(ValueError):
        await tool.execute(subcommand="push")