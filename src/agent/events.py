"""WalkEvent hierarchy — events emitted by PlanWalker through ControlChannel."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.plan import Plan, PlanStep

__all__ = [
    "WalkEvent",
    "PlanStarted",
    "StepStarted",
    "ToolCallStarted",
    "ToolCallCompleted",
    "StepCompleted",
    "StepFailed",
    "AskUser",
    "Paused",
    "Resumed",
    "Aborted",
    "PlanCompleted",
]


class WalkEvent:
    """Marker base class for all walk events — used for isinstance checks."""

    pass


@dataclass
class PlanStarted(WalkEvent):
    """Emitted when the walker begins executing a plan."""

    plan: Plan


@dataclass
class StepStarted(WalkEvent):
    """Emitted before each step begins execution."""

    step: PlanStep
    index: int
    total: int


@dataclass
class ToolCallStarted(WalkEvent):
    """Emitted before a tool call is made within a step."""

    tool: str
    args: dict
    step_id: str


@dataclass
class ToolCallCompleted(WalkEvent):
    """Emitted after a tool call completes."""

    result: Any  # ToolResult — not yet defined; use Any to avoid circular import
    step_id: str


@dataclass
class StepCompleted(WalkEvent):
    """Emitted when a step finishes successfully."""

    step: PlanStep
    result: Any  # StepResult — defined in agent.control (Task 4); use Any to avoid circular


@dataclass
class StepFailed(WalkEvent):
    """Emitted when a step fails."""

    step: PlanStep
    error: str


@dataclass
class AskUser(WalkEvent):
    """Emitted when a step needs user input to proceed."""

    step: PlanStep
    question: str
    options: list[str]


@dataclass
class Paused(WalkEvent):
    """Emitted when the walker is paused — step_id=None means paused mid-plan."""

    step_id: str | None


@dataclass
class Resumed(WalkEvent):
    """Emitted when the walker resumes after a pause."""

    ...


@dataclass
class Aborted(WalkEvent):
    """Emitted when the walker is aborted."""

    reason: str


@dataclass
class PlanCompleted(WalkEvent):
    """Emitted when the walker finishes the plan (all steps done or aborted)."""

    results: list[Any]  # list[StepResult] — StepResult defined in agent.control (Task 4)
