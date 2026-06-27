"""Tests for GrepTool regex search across files."""
from __future__ import annotations

import pytest

from src.tools.grep import GrepTool


@pytest.mark.asyncio
async def test_grep_finds_matches(tmp_path):
    """GrepTool should find regex matches with file:line:content entries."""
    (tmp_path / "a.txt").write_text("hello world\nthis is a needle line\nfoo bar\n")

    tool = GrepTool()
    result = await tool.execute(pattern="needle", path=str(tmp_path))
    matches = result["matches"]

    assert len(matches) == 1
    m = matches[0]
    assert m["path"].endswith("a.txt")
    assert m["line"] == 2
    assert "needle" in m["content"]


@pytest.mark.asyncio
async def test_grep_include_filter(tmp_path):
    """GrepTool include filter should restrict search to matching filenames."""
    (tmp_path / "x.py").write_text("python needle here\n")
    (tmp_path / "x.md").write_text("markdown needle here\n")

    tool = GrepTool()
    result = await tool.execute(pattern="needle", path=str(tmp_path), include="*.py")
    matches = result["matches"]

    assert len(matches) == 1
    assert matches[0]["path"].endswith("x.py")
