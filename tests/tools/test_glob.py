"""Tests for GlobTool recursive pattern matching."""
from __future__ import annotations

import pytest

from src.tools.glob import GlobTool


@pytest.mark.asyncio
async def test_glob_finds_files(tmp_path):
    """GlobTool should find files matching a simple pattern in the given path."""
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "c.txt").write_text("c")

    tool = GlobTool()
    result = await tool.execute(pattern="*.py", path=str(tmp_path))
    paths = result["paths"]

    assert any(p.endswith("a.py") for p in paths)
    assert any(p.endswith("b.py") for p in paths)
    assert not any(p.endswith("c.txt") for p in paths)


@pytest.mark.asyncio
async def test_glob_recursive_double_star(tmp_path):
    """GlobTool with ** should match files in nested subdirectories."""
    (tmp_path / "a.py").write_text("a")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "c.py").write_text("c")
    deep = sub / "deeper"
    deep.mkdir()
    (deep / "d.py").write_text("d")

    tool = GlobTool()
    result = await tool.execute(pattern="**/*.py", path=str(tmp_path))
    paths = result["paths"]

    assert any(p.endswith("a.py") for p in paths)
    assert any(p.endswith("c.py") for p in paths)
    assert any(p.endswith("d.py") for p in paths)
