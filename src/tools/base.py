"""Base classes for tools."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ToolStatus(Enum):
    """Status of tool execution."""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    CONFLICT = "conflict"


@dataclass
class ToolResult:
    """Result returned by tool execution."""

    success: bool
    data: Any = None
    error: Optional[str] = None


class BaseTool:
    """Base class for all tools."""

    name: str = "base"
    description: str = "Base tool"

    def execute(self, *args, **kwargs) -> ToolResult:
        """Execute the tool with given arguments."""
        raise NotImplementedError("Subclasses must implement execute()")
