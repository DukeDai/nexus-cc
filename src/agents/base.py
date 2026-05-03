"""Nexus Agent Base Classes.

Provides BaseAgent abstract class and AgentResult dataclass for all
specialized agents in the Nexus multi-agent system per SPEC.md Section 3.2.

Key Design:
    - All agents return structured AgentResult with confidence scores
    - delegate_task spawns fresh subagents for actual work
    - Each agent has defined toolsets and model tiers
    - Production-ready with full type hints and docstrings
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Optional


class ModelTier(Enum):
    """Model tier selection based on task complexity.

    Per SPEC.md Section 3.2:
        - FAST: Trivial/boilerplate tasks (Haiku)
        - SONNET: Complex reasoning, architecture (Sonnet)
        - OPUS: Maximum capability when needed (Opus)
    """
    FAST = auto()      # Trivial tasks, Haiku tier
    SONNET = auto()    # Complex tasks, Sonnet tier
    OPUS = auto()      # Maximum capability, Opus tier


class AgentRole(Enum):
    """Canonical agent roles in Nexus system."""
    SPECIFIER = auto()   # Requirements → Spec
    IMPLEMENTER = auto() # Spec → Code (TDD)
    REVIEWER = auto()     # Independent verification
    SECURITY = auto()    # Automated security scan


@dataclass
class AgentResult:
    """Structured result returned by all Nexus agents.

    Attributes:
        success: Whether the agent task completed successfully.
        confidence: Confidence score 0.0-1.0 in the result.
        output: The primary output (spec string, code, review, etc.).
        errors: List of error messages if any.
        agent_id: ID of the agent that produced this result.
        duration_seconds: Time taken to produce the result.
        metadata: Additional context-specific data.
        created_at: Timestamp when result was created.
    """
    success: bool
    confidence: float = 0.0
    output: str = ""
    errors: list[str] = field(default_factory=list)
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """Validate confidence score range."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")

    def with_error(self, error: str) -> AgentResult:
        """Return copy with additional error message."""
        new_result = AgentResult(
            success=False,
            confidence=0.0,
            output=self.output,
            errors=[*self.errors, error],
            agent_id=self.agent_id,
            duration_seconds=self.duration_seconds,
            metadata=self.metadata,
        )
        return new_result

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for IPC/storage."""
        return {
            "success": self.success,
            "confidence": self.confidence,
            "output": self.output,
            "errors": self.errors,
            "agent_id": self.agent_id,
            "duration_seconds": self.duration_seconds,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


# Type alias for delegate task functions
DelegateTaskFn = Callable[[dict[str, Any], ModelTier], AgentResult]


@dataclass
class TaskSpec:
    """Specification for a delegated task.

    Attributes:
        task_type: Type identifier for the task.
        requirements: Natural language requirements description.
        context: Additional context (file paths, existing code, etc.).
        constraints: Constraints or requirements to respect.
        priority: Task priority (higher = more important).
    """
    task_type: str
    requirements: str
    context: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    priority: int = 0


class BaseAgent(ABC):
    """Abstract base class for all Nexus agents.

    All specialized agents inherit from BaseAgent and implement:
        - execute(): Core agent logic
        - select_model_tier(): Choose appropriate model tier

    Agents use delegate_task() to spawn fresh subagents for actual work,
    maintaining separation between orchestration and execution.

    Usage:
        result = agent.execute(task_spec)
        result = agent.delegate_task(task_dict, model_tier)

    Attributes:
        role: The agent's canonical role.
        model_tier: Default model tier for this agent.
        tools: List of available tools/capabilities.
        agent_id: Unique identifier for this agent instance.
    """

    def __init__(
        self,
        role: AgentRole,
        model_tier: ModelTier = ModelTier.SONNET,
        tools: Optional[list[str]] = None,
        delegate_fn: Optional[DelegateTaskFn] = None,
    ):
        """Initialize base agent.

        Args:
            role: Canonical role for this agent.
            model_tier: Default model tier.
            tools: Available tool names for this agent.
            delegate_fn: Function to delegate tasks to subagents.
        """
        self.role = role
        self.model_tier = model_tier
        self.tools = tools or []
        self.agent_id = f"{role.name.lower()}_{uuid.uuid4().hex[:8]}"
        self._delegate_fn = delegate_fn

    @abstractmethod
    def execute(self, task: dict[str, Any]) -> AgentResult:
        """Execute the agent's primary task.

        Args:
            task: Task specification dict containing at minimum 'requirements'.

        Returns:
            AgentResult with success status, confidence, and output.
        """
        pass

    @abstractmethod
    def select_model_tier(self, task: dict[str, Any]) -> ModelTier:
        """Select appropriate model tier for task complexity.

        Args:
            task: Task specification dict.

        Returns:
            ModelTier enum value.
        """
        pass

    def delegate_task(
        self,
        task: dict[str, Any],
        model_tier: Optional[ModelTier] = None,
    ) -> AgentResult:
        """Delegate task to a fresh subagent.

        This spawns a new agent instance to handle the actual work,
        maintaining isolation and allowing parallel execution.

        Args:
            task: Task specification dict.
            model_tier: Override default model tier. Uses agent default if None.

        Returns:
            AgentResult from the delegated subagent.
        """
        if self._delegate_fn is None:
            return AgentResult(
                success=False,
                confidence=0.0,
                output="",
                errors=["No delegate function configured"],
                agent_id=self.agent_id,
            )

        tier = model_tier or self.model_tier
        return self._delegate_fn(task, tier)

    def _validate_task(self, task: dict[str, Any]) -> Optional[str]:
        """Validate task dict has required fields.

        Args:
            task: Task specification dict.

        Returns:
            Error message if invalid, None if valid.
        """
        if "requirements" not in task:
            return "Task missing required field: 'requirements'"
        if not isinstance(task["requirements"], str):
            return "'requirements' must be a string"
        return None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(role={self.role.name}, tier={self.model_tier.name}, id={self.agent_id})"
