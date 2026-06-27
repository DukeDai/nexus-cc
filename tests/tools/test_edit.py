"""Tests for EditTool."""
from __future__ import annotations

import pytest

from src.tools.edit import EditTool


@pytest.mark.asyncio
async def test_edit_single_occurrence(tmp_path):
    """Single-occurrence string replacement writes the new file and reports replacements=1."""
    p = tmp_path / "f.txt"
    p.write_text("hello world\nfoo bar\n")
    tool = EditTool()
    result = await tool.execute(path=str(p), old_string="foo bar", new_string="baz qux")
    assert p.read_text() == "hello world\nbaz qux\n"
    assert result == {"path": str(p), "replacements": 1}


@pytest.mark.asyncio
async def test_edit_with_replace_all(tmp_path):
    """replace_all=True replaces every match and reports count."""
    p = tmp_path / "f.txt"
    p.write_text("a-b-c-b-b\n")
    tool = EditTool()
    result = await tool.execute(
        path=str(p), old_string="b", new_string="X", replace_all=True
    )
    assert p.read_text() == "a-X-c-X-X\n"
    assert result == {"path": str(p), "replacements": 3}


@pytest.mark.asyncio
async def test_edit_no_match_raises(tmp_path):
    """ValueError raised when old_string is not in the file."""
    p = tmp_path / "f.txt"
    p.write_text("hello\n")
    tool = EditTool()
    with pytest.raises(ValueError, match="old_string not found"):
        await tool.execute(path=str(p), old_string="missing", new_string="X")


@pytest.mark.asyncio
async def test_edit_multiple_matches_without_replace_all_raises(tmp_path):
    """ValueError raised when old_string matches more than once and replace_all=False."""
    p = tmp_path / "f.txt"
    p.write_text("a-b-a-b-a\n")
    tool = EditTool()
    with pytest.raises(ValueError, match="matches .* locations"):
        await tool.execute(path=str(p), old_string="a", new_string="X")
    # File should be unchanged
    assert p.read_text() == "a-b-a-b-a\n"
