"""Tests for BashTool dangerous-pattern detection and execution."""
from __future__ import annotations

import pytest

from src.tools.bash import BashTool, DangerousCommandError


@pytest.mark.asyncio
async def test_safe_echo():
    """echo hello should run and return exit_code 0 with 'hello' in stdout."""
    tool = BashTool()
    result = await tool.execute(command="echo hello")
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_rm_rf_root_rejected():
    """'rm -fr /' followed by a space should be rejected with DangerousCommandError."""
    tool = BashTool()
    with pytest.raises(DangerousCommandError):
        await tool.execute(command="rm -fr / ")


@pytest.mark.asyncio
async def test_mkfs_rejected():
    """'mkfs.ext4 /dev/sda' should be rejected with DangerousCommandError."""
    tool = BashTool()
    with pytest.raises(DangerousCommandError):
        await tool.execute(command="mkfs.ext4 /dev/sda")


@pytest.mark.asyncio
async def test_exit_code_captured():
    """'false' should return exit_code 1."""
    tool = BashTool()
    result = await tool.execute(command="false")
    assert result["exit_code"] == 1


@pytest.mark.asyncio
async def test_timeout():
    """'sleep 5' with timeout_s=1 should return exit_code -1 and stderr 'timeout'."""
    tool = BashTool()
    result = await tool.execute(command="sleep 5", timeout_s=1)
    assert result["exit_code"] == -1
    assert result["stderr"] == "timeout"
