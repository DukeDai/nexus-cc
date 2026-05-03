"""Tool registry for Nexus engine.

Provides ToolRegistry for managing and executing tools, plus a
convenience ToolBox wrapper with dict-like access.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


class ToolResult:
    """Result of a tool execution."""

    def __init__(self, output: str, error: str | None = None):
        self.output = output
        self.error = error

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def __repr__(self) -> str:
        if self.error:
            return f"ToolResult(error={self.error!r})"
        return f"ToolResult(output={self.output!r})"


class BaseTool:
    """Base class for all Nexus tools."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}

    def __call__(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def definition(self) -> dict[str, Any]:
        """Return Anthropic-style tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Registry for discovering, registering, and executing tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register(self, tool: BaseTool) -> None:
        """Register a single tool instance."""
        if not tool.name:
            raise ValueError(f"Tool {tool!r} has no name")
        self._tools[tool.name] = tool

    def register_all(self, package_name: str = "nexus.tools") -> None:
        """Auto-discover and register all tools in a package.

        Traverses the given package and registers any subclass of BaseTool.
        """
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            # No tools package yet
            return

        for _importer, mod_name, _ispkg in pkgutil.iter_modules(package.__path__):
            module = importlib.import_module(f"{package_name}.{mod_name}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                ):
                    self.register(attr())

    # -------------------------------------------------------------------------
    # Access
    # -------------------------------------------------------------------------

    def get(self, tool_name: str) -> BaseTool | None:
        """Return a tool by name, or None if not found."""
        return self._tools.get(tool_name)

    def list_tools(self) -> list[str]:
        """Return list of registered tool names."""
        return list(self._tools.keys())

    def definitions(self) -> list[dict[str, Any]]:
        """Return Anthropic-style tool definitions for all registered tools."""
        return [tool.definition() for tool in self._tools.values()]

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        """Execute a tool by name with the given arguments."""
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(output="", error=f"Unknown tool: {tool_name}")
        try:
            result = tool(**kwargs)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(output=str(result))
        except Exception as e:
            return ToolResult(output="", error=str(e))


class ToolBox:
    """Dict-like wrapper around ToolRegistry for convenient access.

    Example:
        toolbox = ToolBox(registry)
        bash = toolbox["Bash"]
    """

    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    def __getitem__(self, tool_name: str) -> BaseTool:
        tool = self._registry.get(tool_name)
        if tool is None:
            raise KeyError(f"No tool named {tool_name!r}")
        return tool

    def __contains__(self, tool_name: str) -> bool:
        return self._registry.get(tool_name) is not None

    def __repr__(self) -> str:
        return f"ToolBox({self._registry.list_tools()!r})"
