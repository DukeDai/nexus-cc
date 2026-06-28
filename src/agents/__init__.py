"""Nexus Multi-Agent Specialization System (legacy role files — v1.1+).

Status: KEPT for v1.1 backward compatibility. New code should NOT add
new role files here; instead, extend RoleRegistry in this package or
register custom roles via RoleDefinition from runtime code.

Why this directory still exists in v1.1:
- src/agent/plan.py, walker.py, runtime.py import AgentRole / ModelTier
  types from here (shared cross-cutting enums).
- src/agent/registry.py (RoleRegistry in the new agent namespace)
  registers the default 4 roles by introspecting the agents in this
  package, so deleting it would break RoleRegistry.with_defaults().

If you are looking for the v1.1 plan-first orchestration, see:
- src/agent/runtime.py  — AgentRuntime
- src/agent/planner.py  — Planner (LLM → Plan)
- src/agent/walker.py   — PlanWalker
- src/agent/registry.py — RoleRegistry + RoleDefinition
"""

from .base import (
    AgentRole,
    AgentResult,
    BaseAgent,
    DelegateTaskFn,
    ModelTier,
    TaskSpec,
)
from .registry import RoleDefinition, RoleRegistry
from .default_registry import register_default_roles
from .specifier import SpecifierAgent
from .implementer import ImplementerAgent
from .reviewer import ReviewerAgent
from .security import SecurityAgent

__all__ = [
    "AgentResult",
    "BaseAgent",
    "AgentRole",
    "ModelTier",
    "TaskSpec",
    "DelegateTaskFn",
    "RoleDefinition",
    "RoleRegistry",
    "register_default_roles",
    "SpecifierAgent",
    "ImplementerAgent",
    "ReviewerAgent",
    "SecurityAgent",
]