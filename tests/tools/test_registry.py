"""Tests for ToolRegistry."""
from __future__ import annotations

from typing import Any

import pytest

from src.tools.registry import ToolRegistry


class FakeTool:
    """Minimal tool implementation for testing."""

    def __init__(self, name: str, description: str, args_schema: dict[str, Any], result: Any) -> None:
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self._result = result

    async def execute(self, **kwargs: Any) -> Any:
        return self._result


@pytest.mark.asyncio
async def test_register_and_get():
    """Register a tool with name='X', get it back, assert same instance."""
    registry = ToolRegistry()
    tool = FakeTool("X", "desc", {}, "result")
    registry.register(tool)
    retrieved = registry.get("X")
    assert retrieved is tool


@pytest.mark.asyncio
async def test_all_tools_and_names():
    """Register 2 tools, assert all_tools() returns both and names() returns ['X','Y']."""
    registry = ToolRegistry()
    tool_x = FakeTool("X", "desc X", {}, None)
    tool_y = FakeTool("Y", "desc Y", {}, None)
    registry.register(tool_x)
    registry.register(tool_y)
    all_tools = registry.all_tools()
    names = registry.names()
    assert set(all_tools) == {tool_x, tool_y}
    assert set(names) == {"X", "Y"}


@pytest.mark.asyncio
async def test_execute_invokes_tool():
    """Register a tool whose execute returns a sentinel; call execute and assert sentinel returned."""
    registry = ToolRegistry()
    sentinel = object()
    tool = FakeTool("X", "desc", {"arg": str}, sentinel)
    registry.register(tool)
    result = await registry.execute("X", {"arg": "value"})
    assert result is sentinel


@pytest.mark.asyncio
async def test_get_unknown_raises():
    """Call registry.get('nonexistent'), assert raises KeyError."""
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        registry.get("nonexistent")
