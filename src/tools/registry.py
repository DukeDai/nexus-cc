"""ToolRegistry - catalog of available Tools."""
from __future__ import annotations

from typing import Any

from .base import Tool


class ToolRegistry:
    """Catalog of available Tools, queryable by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool by its name."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Get a tool by name. Raises KeyError if not found."""
        return self._tools[name]

    def all_tools(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    async def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a tool by name with the given arguments.

        Args:
            name: The name of the tool to execute.
            args: Keyword arguments to pass to the tool's execute method.

        Returns:
            The result of the tool's execute method.

        Raises:
            KeyError: If no tool with the given name is registered.
        """
        tool = self.get(name)
        return await tool.execute(**args)
