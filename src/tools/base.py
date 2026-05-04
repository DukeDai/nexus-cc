"""Base classes and protocols for tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable


class ToolStatus(Enum):
    """Status codes for tool execution results."""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    CONFLICT = "conflict"


@dataclass
class ToolResult:
    """Structured result from tool execution.
    
    Attributes:
        success: Whether the operation succeeded.
        status: Detailed status enum value.
        message: Human-readable result message.
        data: Generic data payload (for tools that return data).
        changes: Summary of changes made (for edit operations).
        diff: The actual diff if applicable.
        conflicts: List of detected conflicts.
        metadata: Additional context-specific data.
        created_at: Timestamp of result creation.
    """
    success: bool
    status: ToolStatus = ToolStatus.SUCCESS
    message: str = ""
    data: Any = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    diff: str = ""
    conflicts: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    # Backward-compat alias: 'error' param maps to 'message'
    def __init__(self, success: bool, status: ToolStatus = ToolStatus.SUCCESS,
                 message: str = "", data: Any = None,
                 changes: list[dict[str, Any]] = field(default_factory=list),
                 diff: str = "", conflicts: list[Any] = field(default_factory=list),
                 metadata: dict[str, Any] = field(default_factory=dict),
                 created_at: datetime = field(default_factory=datetime.now),
                 *, error: Optional[str] = None) -> None:
        self.success = success
        self.status = status
        self.message = message or error or ""
        self.data = data
        self.changes = changes
        self.diff = diff
        self.conflicts = conflicts
        self.metadata = metadata
        self.created_at = created_at

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "status": self.status.value,
            "message": self.message,
            "data": self.data,
            "changes": self.changes,
            "diff": self.diff,
            "conflicts": self.conflicts,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@runtime_checkable
class Tool(Protocol):
    """Protocol marking a class as a Nexus tool.
    
    All tools must have 'name' and 'description' attributes.
    The 'execute' method signature is intentionally unconstrained
    since each tool has its own specific arguments.
    """
    name: str
    description: str


# Backward-compat alias
BaseTool = Tool
