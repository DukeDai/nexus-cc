"""Role registry for Nexus v1.1 sub-agent system.

Maps AgentRole to RoleDefinition (system prompt + allowed tools + tier).
Used by PlanWalker to spawn sub-plans for SUBPLAN steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import AgentRole, ModelTier

if TYPE_CHECKING:
    from src.agent.plan import Plan
    from src.agent.runtime import AgentRuntime
    from src.agent.plan import OnFailure


@dataclass
class RoleDefinition:
    """Configuration for a sub-agent role.

    Attributes:
        role: Canonical AgentRole this definition applies to.
        system_prompt: Injected into sub-plan's Planner.
        allowed_tools: ToolRegistry filter for sub-plan.
        model_tier: FAST / SONNET / OPUS for sub-plan LLM calls.
        max_subplan_steps: Cap on sub-plan size to prevent runaway.
        on_subplan_failure: How parent handles sub-plan failure.
    """

    role: AgentRole
    system_prompt: str
    allowed_tools: list[str]
    model_tier: ModelTier
    max_subplan_steps: int = 10
    on_subplan_failure: "OnFailure" = None  # set in __post_init__
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Lazy import to avoid circular dependency at module load.
        from src.agent.plan import OnFailure
        if self.on_subplan_failure is None:
            self.on_subplan_failure = OnFailure.ASK


class RoleRegistry:
    """Registry of role definitions, keyed by AgentRole."""

    def __init__(self, runtime: "AgentRuntime | None"):
        self._runtime = runtime
        self._roles: dict[AgentRole, RoleDefinition] = {}

    def register(self, role: AgentRole, definition: RoleDefinition) -> None:
        """Register or overwrite a role definition."""
        if definition.role != role:
            raise ValueError(
                f"definition.role={definition.role} does not match key={role}"
            )
        self._roles[role] = definition

    def get(self, role: AgentRole) -> RoleDefinition:
        """Get a role definition. Raises KeyError if not registered."""
        if role not in self._roles:
            raise KeyError(f"Role {role.name} not registered")
        return self._roles[role]

    def list_roles(self) -> list[AgentRole]:
        """List all registered roles."""
        return list(self._roles.keys())

    def spawn(self, role: AgentRole, task: str, context: dict[str, Any] | None = None) -> "Plan":
        """Spawn a sub-plan for the given role.

        Args:
            role: The agent role to instantiate.
            task: Natural-language task description.
            context: Optional context dict passed to sub-planner.

        Returns:
            A new Plan ready to be walked.

        Raises:
            RuntimeError: If registry was constructed without a runtime.
        """
        if self._runtime is None:
            raise RuntimeError("RoleRegistry.spawn requires a runtime")
        definition = self.get(role)
        return self._runtime.plan_subplan(
            role=role,
            definition=definition,
            task=task,
            context=context or {},
        )
