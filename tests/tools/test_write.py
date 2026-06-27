"""Tests for WriteTool."""
from __future__ import annotations

import pytest

from src.tools.write import WriteTool


@pytest.mark.asyncio
async def test_write_creates_file(tmp_path):
    """Writing to a new path creates the file and returns dict with path+bytes."""
    p = tmp_path / "out.txt"
    tool = WriteTool()
    result = await tool.execute(path=str(p), content="hello world")
    assert p.read_text() == "hello world"
    assert result == {"path": str(p), "bytes": len("hello world")}


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(tmp_path):
    """Writing to a nested path that does not exist creates parents automatically."""
    p = tmp_path / "a" / "b" / "c" / "out.txt"
    tool = WriteTool()
    result = await tool.execute(path=str(p), content="nested")
    assert p.read_text() == "nested"
    assert (tmp_path / "a" / "b").is_dir()
    assert result["path"] == str(p)
    assert result["bytes"] == len("nested")


@pytest.mark.asyncio
async def test_write_overwrites_existing(tmp_path):
    """Writing to an existing file overwrites its content."""
    p = tmp_path / "out.txt"
    p.write_text("old content")
    tool = WriteTool()
    result = await tool.execute(path=str(p), content="new content")
    assert p.read_text() == "new content"
    assert result["bytes"] == len("new content")
