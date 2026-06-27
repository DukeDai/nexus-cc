"""Tests for ReadTool."""
from __future__ import annotations

import pytest

from src.tools.read import ReadTool


@pytest.mark.asyncio
async def test_read_whole_file(tmp_path):
    """Read a file with no start_line/end_line returns the full text."""
    p = tmp_path / "hello.txt"
    p.write_text("alpha\nbeta\ngamma\n")
    tool = ReadTool()
    result = await tool.execute(path=str(p))
    assert result == "alpha\nbeta\ngamma\n"


@pytest.mark.asyncio
async def test_read_line_range(tmp_path):
    """Read a file with start_line=2, end_line=3 returns just those lines."""
    p = tmp_path / "hello.txt"
    p.write_text("a\nb\nc\nd\ne\n")
    tool = ReadTool()
    result = await tool.execute(path=str(p), start_line=2, end_line=3)
    assert result == "b\nc\n"


@pytest.mark.asyncio
async def test_read_missing_file_raises(tmp_path):
    """Reading a non-existent file raises FileNotFoundError."""
    p = tmp_path / "does_not_exist.txt"
    tool = ReadTool()
    with pytest.raises(FileNotFoundError):
        await tool.execute(path=str(p))
