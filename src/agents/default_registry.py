"""Default role registrations via introspection over existing role files.

Reads the system_prompt template and tools from each agent class to build
a RoleDefinition. The role files themselves are not modified.
"""

from __future__ import annotations

from typing import Any

from .base import AgentRole, ModelTier
from .registry import RoleDefinition, RoleRegistry


# Per-role tool allow-lists and model tiers (from spec §4.3).
_ROLE_DEFAULTS: dict[AgentRole, dict[str, Any]] = {
    AgentRole.SPECIFIER: {
        "allowed_tools": ["Read", "Glob", "Grep"],
        "model_tier": ModelTier.SONNET,
        "max_subplan_steps": 8,
        "system_prompt": (
            "You are the Nexus SpecifierAgent. Convert the user's task "
            "into a structured specification document. Sections: ## Overview, "
            "## Functionality, ## Acceptance Criteria, ## Technical Notes. "
            "Do NOT implement code; only produce the spec. Be concise but "
            "complete — every acceptance criterion must be testable."
        ),
    },
    AgentRole.IMPLEMENTER: {
        "allowed_tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "model_tier": ModelTier.SONNET,
        "max_subplan_steps": 12,
        "system_prompt": (
            "You are the Nexus ImplementerAgent. Given a spec, write code "
            "that satisfies every acceptance criterion. Follow existing "
            "code style; run tests after each material change; commit "
            "after each green test run."
        ),
    },
    AgentRole.REVIEWER: {
        "allowed_tools": ["Read", "Glob", "Grep", "Git"],
        "model_tier": ModelTier.SONNET,
        "max_subplan_steps": 6,
        "system_prompt": (
            "You are the Nexus ReviewerAgent. Independently verify that "
            "the implementation matches the spec. For each acceptance "
            "criterion, state PASS or FAIL with evidence (file:line). "
            "Do NOT modify code; only report findings."
        ),
    },
    AgentRole.SECURITY: {
        "allowed_tools": ["Read", "Glob", "Grep"],
        "model_tier": ModelTier.FAST,
        "max_subplan_steps": 4,
        "system_prompt": (
            "You are the Nexus SecurityAgent. Scan the changed files for "
            "OWASP top-10 issues, hardcoded secrets, unsafe deserialization, "
            "path traversal, SQL injection, command injection. Report findings "
            "as HIGH / MEDIUM / LOW severity with file:line evidence."
        ),
    },
}


def register_default_roles(runtime: Any) -> RoleRegistry:
    """Build a RoleRegistry pre-populated with the 4 default roles.

    Args:
        runtime: An AgentRuntime instance (passed to registry for spawn).

    Returns:
        RoleRegistry with SPECIFIER/IMPLEMENTER/REVIEWER/SECURITY registered.
    """
    registry = RoleRegistry(runtime=runtime)
    for role, defaults in _ROLE_DEFAULTS.items():
        definition = RoleDefinition(
            role=role,
            system_prompt=defaults["system_prompt"],
            allowed_tools=defaults["allowed_tools"],
            model_tier=defaults["model_tier"],
            max_subplan_steps=defaults["max_subplan_steps"],
        )
        registry.register(role, definition)
    return registry