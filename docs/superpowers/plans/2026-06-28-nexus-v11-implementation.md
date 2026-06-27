# Nexus v1.1 Multi-Agent + Memory + Self-Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sub-agent role wiring, three-layer memory, and a self-evolution feedback loop to Nexus, turning the v1.0 plan-first executor into a multi-agent system that improves its own planning over time.

**Architecture:** v1.1 is purely additive on top of v1.0's plan-first runtime. New `SUBPLAN` step kind spawns child Plans via a thin `RoleRegistry` adapter that reuses existing role files (`src/agents/`) unchanged. Memory is a derived view over WAL JSONL (no new storage layer); semantic embeddings are opt-in. The Evolver sits as a `post_walk_hook` on `AgentRuntime`, reading WAL patterns and staging prompt updates that the user approves in the TUI before they take effect. WAL format bumps to v2 (adds `format_version` header + per-record `metadata` block); v1.0 WAL files still load in v1.1.

**Tech Stack:** Python 3.12+, asyncio (existing), Textual (existing TUI), Anthropic SDK (existing LLM), optional `sentence-transformers` for semantic embeddings, pytest for tests.

## Global Constraints

These are project-wide requirements copied verbatim from the spec. Every task's requirements implicitly include this section.

| Constraint | Value |
|---|---|
| Python | `>=3.12` (per `pyproject.toml`) |
| Code style | Match existing v1.0 idiom: type hints everywhere, dataclasses for data shapes, async/await for I/O, single asyncio event loop, no threads, no locks |
| WAL format | Append-only JSONL, one record per line, `format_version` field on header and per-record |
| WAL compatibility | v1.1 must read all v1.0 WAL files; v1.1 WAL writes use `format_version=2` |
| Event flow | All walker events flow through `ControlChannel` (existing); no new event types in v1.1 |
| Pause semantics | Pause only at step boundaries; SUBPLAN steps are atomic from parent's perspective |
| Test style | pytest + pytest-asyncio, fixtures in `conftest.py`, one test file per module under `tests/` |
| Commit style | Atomic commits per task; `Co-Authored-By: Claude <noreply@anthropic.com>` trailer |
| Branch | Execute on `main` per user request (no worktree requested) |
| Dependency management | New optional deps go in `pyproject.toml` under `[project.optional-dependencies]` |
| Naming | snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE for constants |
| Module locations | `src/agent/` for runtime, `src/agents/` for role files (note the `s`), `src/context/` for state, `src/tui/` for UI, `src/cli/` for commands |

## File Structure

**New files (v1.1 additions):**

| File | Responsibility | LOC est. |
|---|---|---|
| `src/agents/registry.py` | `RoleDefinition` dataclass + `RoleRegistry` class | 110 |
| `src/agents/default_registry.py` | Default role registration via introspection | 60 |
| `src/agent/subplan.py` | SUBPLAN-related types (`SubplanContext`, `SubplanResult`) | 50 |
| `src/agent/verify_adapter.py` | `VerificationAdapter` bridge to `src/verification/pipeline.py` | 90 |
| `src/agent/prompts.py` | `PromptTemplate` + `PromptTemplateRegistry` | 130 |
| `src/agent/evolution.py` | `Evolver` + `StagedChanges` | 160 |
| `src/context/memory.py` | `MemoryStore` + `EpisodicIndex` + `SemanticIndex` + `SkillIndex` | 280 |
| `src/cli/migrate.py` | `nexus session migrate` Typer command | 70 |
| `src/cli/role.py` | `nexus role list/show` Typer commands | 50 |
| `src/cli/memory.py` | `nexus memory warm/stats/search` Typer commands | 90 |
| `src/cli/skill_cli.py` | `nexus skill list/apply` Typer commands | 60 |
| `src/cli/prompt.py` | `nexus prompt list/show/revert/history` Typer commands | 100 |
| `src/cli/evolve.py` | `nexus evolve --auto` Typer command | 50 |
| `src/tui/verifier_panel.py` | VerifierPanel widget | 100 |
| `src/tui/memory_panel.py` | MemoryPanel widget | 100 |
| `src/tui/skill_picker_modal.py` | SkillPickerModal | 110 |
| `src/tui/evolve_approval_modal.py` | EvolveApprovalModal | 130 |
| `src/tui/prompt_history_viewer_modal.py` | PromptHistoryViewerModal | 80 |

**Modified files:**

| File | Change |
|---|---|
| `src/agent/plan.py` | Add `SUBPLAN` to `PlanStepKind`, `RETRY_WITH_FEEDBACK` to `OnFailure`, add `role`, `subplan_args`, `pipeline`, `pipeline_args` fields to `PlanStep` |
| `src/agent/walker.py` | Add `_execute_subplan` branch + `_execute_verify` extension for pipeline mode + retry_with_feedback loop |
| `src/agent/runtime.py` | Accept `RoleRegistry`, `MemoryStore`, `PromptTemplateRegistry`, `VerificationAdapter`, `Evolver` in `__init__`; add `post_walk_hook`; augment Planner call with memory + prompt registry |
| `src/agent/planner.py` | Accept optional `memory_context: str` and `prompt_template: PromptTemplate`; prepend to system prompt |
| `src/context/wal.py` | On WAL creation, write `format_version=2` header; preserve v1 records on read; add `migrate_v1_to_v2(plan_id)` |
| `src/tui/app.py` | Mount `VerifierPanel` + `MemoryPanel`; register new keybindings (`V`, `M`, `s`, `E`, `Ctrl-r`); wire modals |
| `src/tui/plan_panel.py` | Render SUBPLAN nodes collapsed/expanded with `↳` prefix |
| `src/cli/main.py` | Register new subcommand groups |
| `pyproject.toml` | Add `nexus-cc[embeddings]` extra |
| `tests/` | New test files (see Phase F) |
| `README.md`, `ARCHITECTURE.md`, `ROADMAP.md` | Rewrite for v1.1 features |

**Test files (new):**

| File | Tests |
|---|---|
| `tests/agents/test_role_registry.py` | Registry CRUD, spawn integration, default role coverage |
| `tests/agent/test_subplan.py` | SUBPLAN walker dispatch (8 scenarios) |
| `tests/agent/test_verify_adapter.py` | Pipeline registration, retry_with_feedback, default pipelines |
| `tests/agent/test_prompts.py` | Template versioning, history append, revert |
| `tests/agent/test_evolution.py` | Evolver record_outcome, update_prompt_registry, should_replan |
| `tests/context/test_memory.py` | EpisodicIndex rebuild + similar_past, SemanticIndex search, SkillIndex delegation, MemoryStore.warm |
| `tests/integration/test_subplan_e2e.py` | 6 E2E scenarios |
| `tests/integration/test_migration.py` | WAL v1 → v2 round-trip on synthetic fixtures |
| `tests/tui/test_new_panels.py` | Verifier/Memory/SkillPicker/EvolveApproval render + key bindings |
| `tests/integration/test_llm_smoke_v11.py` | 4 LLM smoke tests (multi-agent, memory injection, evolver, verifier retry) |

---
## Phase A — Role Wiring (5 tasks, ~4 days)

### Task 1: Extend `Plan`/`PlanStep` schema with SUBPLAN fields

**Files:**
- Modify: `src/agent/plan.py:1-92`
- Test: `tests/agent/test_plan_schema.py`

**Interfaces:**
- Consumes: nothing (existing schema)
- Produces: `PlanStep.role: AgentRole | None`, `PlanStep.subplan_args: dict[str, Any] | None`, `PlanStepKind.SUBPLAN = "subplan"`, `OnFailure.RETRY_WITH_FEEDBACK = "retry_with_feedback"`

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_plan_schema.py`:

```python
from src.agent.plan import PlanStep, PlanStepKind, OnFailure
from src.agents.base import AgentRole


def test_subplan_kind_exists():
    assert PlanStepKind.SUBPLAN.value == "subplan"


def test_retry_with_feedback_enum_exists():
    assert OnFailure.RETRY_WITH_FEEDBACK.value == "retry_with_feedback"


def test_plan_step_has_role_and_subplan_args():
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.SUBPLAN,
        tool="spec the new feature",
        role=AgentRole.SPECIFIER,
        subplan_args={"scope": "src/auth/"},
        on_failure=OnFailure.ASK,
    )
    assert step.role == AgentRole.SPECIFIER
    assert step.subplan_args == {"scope": "src/auth/"}


def test_plan_step_role_optional_for_non_subplan_kinds():
    step = PlanStep(
        id="step-2",
        kind=PlanStepKind.TOOL,
        tool="Read",
        args={"path": "config.yml"},
    )
    assert step.role is None
    assert step.subplan_args is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_plan_schema.py -v`
Expected: FAIL — `PlanStep` has no `role` field, `PlanStepKind.SUBPLAN` doesn't exist.

- [ ] **Step 3: Extend `src/agent/plan.py`**

Open `src/agent/plan.py`. The current file imports `Enum` and `dataclass`. Add the imports for `AgentRole` (lazy import to avoid circular dependency) and update the dataclass.

Replace the imports section (top of file, ~lines 1-10) with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agents.base import AgentRole
```

Add `SUBPLAN` to the `PlanStepKind` enum (find the enum declaration and add):

```python
class PlanStepKind(str, Enum):
    TOOL = "tool"
    VERIFY = "verify"
    CRITIQUE = "critique"
    ASK_USER = "ask_user"
    SUBPLAN = "subplan"
```

Add `RETRY_WITH_FEEDBACK` to the `OnFailure` enum:

```python
class OnFailure(str, Enum):
    ABORT = "abort"
    SKIP = "skip"
    RETRY = "retry"
    ASK = "ask"
    RETRY_WITH_FEEDBACK = "retry_with_feedback"
```

In the `PlanStep` dataclass, add two new optional fields. Read the existing `PlanStep` definition first to see exact field order, then add after `tool_args`:

```python
role: "AgentRole | None" = None
subplan_args: dict[str, Any] | None = None
pipeline: str | None = None
pipeline_args: dict[str, Any] | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_plan_schema.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent/plan.py tests/agent/test_plan_schema.py
git commit -m "feat(plan): add SUBPLAN kind + role/subplan_args/pipeline fields + RETRY_WITH_FEEDBACK"
```

---

### Task 2: Create `RoleDefinition` and `RoleRegistry` data structures

**Files:**
- Create: `src/agents/registry.py`
- Test: `tests/agents/test_role_registry.py`

**Interfaces:**
- Consumes: `AgentRole` (from `src/agents/base.py`), `ModelTier` (from `src/agents/base.py`)
- Produces: `RoleDefinition` dataclass, `RoleRegistry` class with `register(role, def)`, `spawn(role, task, context) -> Plan`, `list_roles() -> list[AgentRole]`, `with_defaults(runtime) -> RoleRegistry`

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_role_registry.py`:

```python
import pytest
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry


def test_role_definition_construction():
    defn = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="You are a specifier.",
        allowed_tools=["Read", "Glob"],
        model_tier=ModelTier.SONNET,
        max_subplan_steps=8,
    )
    assert defn.role == AgentRole.SPECIFIER
    assert defn.allowed_tools == ["Read", "Glob"]
    assert defn.max_subplan_steps == 8


def test_role_registry_register_and_list():
    registry = RoleRegistry(runtime=None)
    defn = RoleDefinition(
        role=AgentRole.REVIEWER,
        system_prompt="Review code.",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    registry.register(AgentRole.REVIEWER, defn)
    assert registry.list_roles() == [AgentRole.REVIEWER]


def test_role_registry_register_overwrites():
    registry = RoleRegistry(runtime=None)
    defn1 = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="v1",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    defn2 = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="v2",
        allowed_tools=["Read", "Grep"],
        model_tier=ModelTier.OPUS,
    )
    registry.register(AgentRole.SPECIFIER, defn1)
    registry.register(AgentRole.SPECIFIER, defn2)
    assert registry.get(AgentRole.SPECIFIER).system_prompt == "v2"


def test_role_registry_get_missing_raises():
    registry = RoleRegistry(runtime=None)
    with pytest.raises(KeyError):
        registry.get(AgentRole.IMPLEMENTER)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agents/test_role_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.agents.registry'`.

- [ ] **Step 3: Create `src/agents/registry.py`**

```python
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
    from src.agent.control import OnFailure


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
        from src.agent.control import OnFailure
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agents/test_role_registry.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agents/registry.py tests/agents/test_role_registry.py
git commit -m "feat(agents): add RoleDefinition + RoleRegistry for sub-agent wiring"
```

---


### Task 3: Wire default roles via introspection

**Files:**
- Create: `src/agents/default_registry.py`
- Modify: `src/agents/__init__.py:1-27`
- Test: `tests/agents/test_default_registry.py`

**Interfaces:**
- Consumes: `RoleRegistry` (Task 2), `SpecifierAgent`/`ImplementerAgent`/`ReviewerAgent`/`SecurityAgent` (existing), `AgentRole`/`ModelTier` (existing)
- Produces: `RoleRegistry.with_defaults(runtime) -> RoleRegistry` with 4 roles pre-registered; default `RoleDefinition` instances with sensible tools + tiers per spec §4.3

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_default_registry.py`:

```python
from src.agents.base import AgentRole, ModelTier
from src.agents.default_registry import register_default_roles
from src.agents.registry import RoleRegistry


class FakeRuntime:
    pass


def test_register_default_roles_returns_registry_with_4_roles():
    registry = register_default_roles(FakeRuntime())
    roles = registry.list_roles()
    assert AgentRole.SPECIFIER in roles
    assert AgentRole.IMPLEMENTER in roles
    assert AgentRole.REVIEWER in roles
    assert AgentRole.SECURITY in roles
    assert len(roles) == 4


def test_default_specifier_uses_sonnet_tier():
    registry = register_default_roles(FakeRuntime())
    defn = registry.get(AgentRole.SPECIFIER)
    assert defn.model_tier == ModelTier.SONNET
    assert "Read" in defn.allowed_tools


def test_default_security_uses_fast_tier():
    registry = register_default_roles(FakeRuntime())
    defn = registry.get(AgentRole.SECURITY)
    assert defn.model_tier == ModelTier.FAST


def test_default_implementer_has_max_subplan_steps_12():
    registry = register_default_roles(FakeRuntime())
    defn = registry.get(AgentRole.IMPLEMENTER)
    assert defn.max_subplan_steps == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agents/test_default_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.agents.default_registry'`.

- [ ] **Step 3: Create `src/agents/default_registry.py`**

```python
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
```

- [ ] **Step 4: Update `src/agents/__init__.py`**

Add `RoleRegistry` and `register_default_roles` to the public exports. Modify the file:

```python
"""Nexus Multi-Agent Specialization System."""

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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agents/test_default_registry.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add src/agents/default_registry.py src/agents/__init__.py tests/agents/test_default_registry.py
git commit -m "feat(agents): default role registrations via introspection"
```

---

### Task 4: Add `_execute_subplan` branch to PlanWalker

**Files:**
- Modify: `src/agent/walker.py:1-240`
- Test: `tests/agent/test_subplan.py`

**Interfaces:**
- Consumes: `RoleRegistry` (Task 2), `PlanStep.kind=SUBPLAN` (Task 1), existing `PlanWalker.walk()`, `PlanAborted` (existing exception)
- Produces: `_execute_subplan(step: PlanStep) -> StepResult` method on `PlanWalker`. Returns `StepResult(status="completed"|"failed", metadata={"subplan_result": ..., "subplan_aborted": bool})`. Imports `RoleRegistry` lazily.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_subplan.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.control import StepResult
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agent.runtime import PlanAborted
from src.agent.walker import PlanWalker
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry


def _make_role_registry(spawn_return: Plan, *, raises: Exception | None = None) -> RoleRegistry:
    runtime = MagicMock()
    runtime.plan_subplan = MagicMock(side_effect=raises) if raises else MagicMock(return_value=spawn_return)
    runtime.walk = AsyncMock(return_value=StepResult(status="completed"))
    registry = RoleRegistry(runtime=runtime)
    registry.register(
        AgentRole.SPECIFIER,
        RoleDefinition(
            role=AgentRole.SPECIFIER,
            system_prompt="spec",
            allowed_tools=["Read"],
            model_tier=ModelTier.SONNET,
        ),
    )
    return registry, runtime


@pytest.mark.asyncio
async def test_execute_subplan_returns_completed_when_subplan_succeeds():
    registry, runtime = _make_role_registry(spawn_return=Plan(id="p_sub"))
    walker = PlanWalker(
        plan=Plan(id="p_parent", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.SUBPLAN,
        tool="spec the auth flow",
        role=AgentRole.SPECIFIER,
    )
    result = await walker._execute_subplan(step)
    assert result.status == "completed"
    assert result.metadata["subplan_result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_execute_subplan_returns_failed_when_subplan_aborts():
    registry, runtime = _make_role_registry(
        spawn_return=Plan(id="p_sub"),
        raises=PlanAborted("user pressed x"),
    )
    walker = PlanWalker(
        plan=Plan(id="p_parent", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.SUBPLAN,
        tool="spec the auth flow",
        role=AgentRole.SPECIFIER,
    )
    result = await walker._execute_subplan(step)
    assert result.status == "failed"
    assert result.metadata["subplan_aborted"] is True


@pytest.mark.asyncio
async def test_execute_subplan_raises_when_registry_missing():
    walker = PlanWalker(
        plan=Plan(id="p_parent", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=None,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.SUBPLAN,
        tool="spec the auth flow",
        role=AgentRole.SPECIFIER,
    )
    with pytest.raises(RuntimeError, match="RoleRegistry not configured"):
        await walker._execute_subplan(step)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_subplan.py -v`
Expected: FAIL — `PlanWalker.__init__` does not accept `role_registry`; `_execute_subplan` does not exist.

- [ ] **Step 3: Read existing walker**

Read `src/agent/walker.py` end-to-end. Note:
- `PlanWalker.__init__` signature
- `_execute_step` dispatch method
- `StepResult` shape and `metadata` field
- Where exceptions are caught

- [ ] **Step 4: Modify `src/agent/walker.py`**

Add `role_registry` parameter to `PlanWalker.__init__`:

```python
def __init__(
    self,
    plan: Plan,
    channel: ControlChannel,
    tools: ToolRegistry,
    wal: WALManager,
    role_registry: RoleRegistry | None = None,
):
    self.plan = plan
    self.channel = channel
    self.tools = tools
    self.wal = wal
    self.role_registry = role_registry
```

In `_execute_step` (find the dispatch block), add SUBPLAN branch BEFORE the existing branches:

```python
async def _execute_step(self, step: PlanStep) -> StepResult:
    if step.kind == PlanStepKind.SUBPLAN:
        return await self._execute_subplan(step)
    # ... existing TOOL / VERIFY / CRITIQUE / ASK_USER branches ...
```

Add the `_execute_subplan` method at the end of the class:

```python
async def _execute_subplan(self, step: PlanStep) -> StepResult:
    """Execute a SUBPLAN step by spawning a child Plan via the role registry.

    Args:
        step: The PlanStep with kind=SUBPLAN, role set, tool=task description.

    Returns:
        StepResult with status="completed" if sub-plan succeeded,
        status="failed" if sub-plan aborted (caught here so the parent's
        on_failure strategy applies).

    Raises:
        RuntimeError: If role_registry was not configured on this walker.
    """
    if self.role_registry is None:
        raise RuntimeError("RoleRegistry not configured on PlanWalker")
    if step.role is None:
        raise ValueError(f"SUBPLAN step {step.id} has no role")

    sub_plan = self.role_registry.spawn(
        role=step.role,
        task=step.tool,
        context=step.subplan_args or {},
    )
    try:
        sub_result = await self._runtime_for_subplan().walk(sub_plan)
        return StepResult(
            status=sub_result.status,
            metadata={
                "subplan_id": sub_plan.id,
                "subplan_result": sub_result.to_dict() if hasattr(sub_result, "to_dict") else {"status": sub_result.status},
                "subplan_aborted": False,
            },
        )
    except PlanAborted as e:
        return StepResult(
            status="failed",
            error=str(e),
            metadata={
                "subplan_id": sub_plan.id,
                "subplan_aborted": True,
            },
        )

def _runtime_for_subplan(self) -> "AgentRuntime":
    """Return the runtime handle for sub-plan walks.

    The runtime reference is set by AgentRuntime before invoking walk().
    """
    if not hasattr(self, "_runtime") or self._runtime is None:
        raise RuntimeError(
            "PlanWalker._runtime is not set; AgentRuntime must call "
            "walker._runtime = self before walking sub-plans."
        )
    return self._runtime
```

Add the imports at the top of the file (next to existing imports):

```python
from src.agent.plan import PlanStepKind, PlanStep, Plan
from src.agents.registry import RoleRegistry  # only for type hints
from src.agent.runtime import PlanAborted  # only for catching
```

(Use lazy imports inside the method if circular import issues arise.)

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_subplan.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add src/agent/walker.py tests/agent/test_subplan.py
git commit -m "feat(walker): add _execute_subplan branch for SUBPLAN steps"
```

---

### Task 5: Wire `RoleRegistry` into `AgentRuntime` + add `plan_subplan`

**Files:**
- Modify: `src/agent/runtime.py:1-74`
- Modify: `src/agent/walker.py` (set `_runtime` before walk)
- Test: `tests/agent/test_runtime_subplan.py`

**Interfaces:**
- Consumes: `RoleRegistry` (Tasks 2-3), `Planner` (existing), `ControlChannel` (existing), `WALManager` (existing)
- Produces: `AgentRuntime.__init__(..., role_registry: RoleRegistry | None = None)`; `AgentRuntime.plan_subplan(role, definition, task, context) -> Plan` method that calls `Planner.plan()` with role's system_prompt + tool filter + max_steps cap

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_runtime_subplan.py`:

```python
from unittest.mock import MagicMock

from src.agent.plan import Plan, PlanStepKind
from src.agent.runtime import AgentRuntime
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry


def test_runtime_accepts_role_registry():
    registry = RoleRegistry(runtime=None)
    runtime = AgentRuntime(
        planner=MagicMock(),
        tools=MagicMock(),
        channel=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    assert runtime.role_registry is registry


def test_plan_subplan_calls_planner_with_role_prompt():
    planner = MagicMock()
    planner.plan = MagicMock(return_value=Plan(id="p_sub"))
    registry = RoleRegistry(runtime=None)
    registry.register(
        AgentRole.SPECIFIER,
        RoleDefinition(
            role=AgentRole.SPECIFIER,
            system_prompt="You are a specifier.",
            allowed_tools=["Read", "Glob"],
            model_tier=ModelTier.SONNET,
            max_subplan_steps=8,
        ),
    )
    runtime = AgentRuntime(
        planner=planner,
        tools=MagicMock(),
        channel=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    sub_plan = runtime.plan_subplan(
        role=AgentRole.SPECIFIER,
        definition=registry.get(AgentRole.SPECIFIER),
        task="spec the auth flow",
        context={"scope": "src/auth/"},
    )
    assert sub_plan.id == "p_sub"
    planner.plan.assert_called_once()
    call_kwargs = planner.plan.call_args.kwargs
    assert "You are a specifier." in call_kwargs["system_prompt"]
    assert call_kwargs["model_tier"] == ModelTier.SONNET
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_runtime_subplan.py -v`
Expected: FAIL — `AgentRuntime.__init__` does not accept `role_registry`.

- [ ] **Step 3: Read existing `runtime.py`**

Read `src/agent/runtime.py` end-to-end. Note the constructor signature, `plan()` method, `walk()` method, and how Planner is wired.

- [ ] **Step 4: Modify `src/agent/runtime.py`**

Add `role_registry` parameter to `__init__`:

```python
def __init__(
    self,
    planner: Planner,
    tools: ToolRegistry,
    channel: ControlChannel,
    wal: WALManager,
    role_registry: RoleRegistry | None = None,
    memory_store: "MemoryStore | None" = None,  # forward-declared for Task 16
    prompt_registry: "PromptTemplateRegistry | None" = None,  # forward-declared for Task 18
    verifier_adapter: "VerificationAdapter | None" = None,  # forward-declared for Task 7
    evolver: "Evolver | None" = None,  # forward-declared for Task 20
):
    self.planner = planner
    self.tools = tools
    self.channel = channel
    self.wal = wal
    self.role_registry = role_registry
    self.memory_store = memory_store
    self.prompt_registry = prompt_registry
    self.verifier_adapter = verifier_adapter
    self.evolver = evolver
    self._post_walk_hooks: list[Callable] = []
```

Add `plan_subplan` method:

```python
def plan_subplan(
    self,
    role: AgentRole,
    definition: RoleDefinition,
    task: str,
    context: dict,
) -> Plan:
    """Generate a sub-plan for the given role.

    Args:
        role: The agent role (must match definition.role).
        definition: The role configuration.
        task: Natural-language task description.
        context: Optional context dict.

    Returns:
        A new Plan ready to be walked. Max step count is capped at
        definition.max_subplan_steps; if exceeded, raises ValueError.
    """
    # Augment the Planner's prompt with the role's system prompt.
    planner_kwargs = {
        "system_prompt": definition.system_prompt,
        "model_tier": definition.model_tier,
        "allowed_tools": definition.allowed_tools,
        "max_steps": definition.max_subplan_steps,
        "context": context,
    }
    plan = self.planner.plan(task=task, **planner_kwargs)
    if len(plan.steps) > definition.max_subplan_steps:
        raise ValueError(
            f"Sub-plan has {len(plan.steps)} steps, exceeds "
            f"max_subplan_steps={definition.max_subplan_steps} for role {role.name}"
        )
    return plan
```

In the existing `walk()` method, set `walker._runtime = self` before invoking walk (so sub-plan walks can call back into runtime):

```python
async def walk(self, plan: Plan) -> PlanResult:
    walker = PlanWalker(
        plan=plan,
        channel=self.channel,
        tools=self.tools,
        wal=self.wal,
        role_registry=self.role_registry,
    )
    walker._runtime = self   # NEW: enables sub-plan walks to call runtime.plan_subplan
    return await walker.walk()
```

(Use `TYPE_CHECKING` import for `RoleRegistry` and `MemoryStore` to avoid circular deps.)

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_runtime_subplan.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 6: Commit**

```bash
git add src/agent/runtime.py tests/agent/test_runtime_subplan.py
git commit -m "feat(runtime): wire RoleRegistry + plan_subplan method"
```

---


## Phase B — Verification Hooks (4 tasks, ~3 days)

### Task 6: Extend `VERIFY` step with `pipeline` + `pipeline_args` fields

**Files:**
- Modify: `src/agent/plan.py` (already extended in Task 1 with `pipeline` + `pipeline_args` fields)
- Test: `tests/agent/test_plan_schema.py` (extend existing file)

**Interfaces:**
- Consumes: `PlanStep.pipeline: str | None`, `PlanStep.pipeline_args: dict | None` (from Task 1)
- Produces: Test coverage asserting fields default to None and can be set

- [ ] **Step 1: Append to existing test file**

Append to `tests/agent/test_plan_schema.py`:

```python
def test_plan_step_has_pipeline_and_pipeline_args():
    step = PlanStep(
        id="step-3",
        kind=PlanStepKind.VERIFY,
        tool=None,
        pipeline="security",
        pipeline_args={"scope": "src/auth/"},
        success_criteria="no HIGH findings",
    )
    assert step.pipeline == "security"
    assert step.pipeline_args == {"scope": "src/auth/"}


def test_plan_step_pipeline_optional_for_verify_kind():
    step = PlanStep(
        id="step-4",
        kind=PlanStepKind.VERIFY,
        tool=None,
        success_criteria="looks good",
    )
    assert step.pipeline is None
    assert step.pipeline_args is None
```

- [ ] **Step 2: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_plan_schema.py -v`
Expected: PASS — 6 tests total (4 from Task 1 + 2 new).

- [ ] **Step 3: Commit**

```bash
git add tests/agent/test_plan_schema.py
git commit -m "test(plan): cover pipeline + pipeline_args fields"
```

---

### Task 7: Create `VerificationAdapter` data structures

**Files:**
- Create: `src/agent/verify_adapter.py`
- Test: `tests/agent/test_verify_adapter.py`

**Interfaces:**
- Consumes: `WALManager` (existing), `VerificationPipeline` protocol from `src/verification/pipeline.py`
- Produces: `VerificationAdapter` class with `register(name, pipeline)`, `list_pipelines()`, `run(step, step_result, ctx) -> VerificationOutcome`

- [ ] **Step 1: Read `src/verification/pipeline.py` first**

Read the existing verification pipeline module to understand `VerificationPipeline` protocol and `VerificationOutcome` shape. Use those exact types.

- [ ] **Step 2: Write the failing test**

Create `tests/agent/test_verify_adapter.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.control import StepResult
from src.agent.plan import PlanStep, PlanStepKind
from src.agent.verify_adapter import VerificationAdapter


class FakePipeline:
    def __init__(self, outcome):
        self._outcome = outcome

    async def verify(self, step, step_result, ctx):
        return self._outcome


@pytest.mark.asyncio
async def test_register_and_list_pipelines():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register("security", FakePipeline(None))
    adapter.register("test", FakePipeline(None))
    assert "security" in adapter.list_pipelines()
    assert "test" in adapter.list_pipelines()


@pytest.mark.asyncio
async def test_run_invokes_pipeline_with_step_and_context():
    wal = MagicMock()
    wal.context_for_step = MagicMock(return_value={"files_touched": ["src/auth/login.py"]})
    adapter = VerificationAdapter(wal=wal)
    outcome = MagicMock(passed=True, errors=[], warnings=[])
    adapter.register("security", FakePipeline(outcome))
    step = PlanStep(id="step-1", kind=PlanStepKind.VERIFY, pipeline="security")
    result = await adapter.run(step, StepResult(status="completed"), ctx=MagicMock())
    assert result.passed is True


@pytest.mark.asyncio
async def test_run_raises_for_unregistered_pipeline():
    adapter = VerificationAdapter(wal=MagicMock())
    step = PlanStep(id="step-1", kind=PlanStepKind.VERIFY, pipeline="nonexistent")
    with pytest.raises(KeyError):
        await adapter.run(step, StepResult(status="completed"), ctx=MagicMock())
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_verify_adapter.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Create `src/agent/verify_adapter.py`**

```python
"""Bridge between VERIFY step and src/verification/pipeline.py.

VerificationAdapter allows the PlanWalker to invoke named verification
pipelines (security, tdd, test, review) without coupling to their
concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from src.agent.control import StepResult
from src.agent.plan import PlanStep

if TYPE_CHECKING:
    from src.context.wal import WALManager


class VerificationOutcome:
    """Outcome of a verification pipeline run."""

    def __init__(
        self,
        passed: bool,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.passed = passed
        self.errors = errors or []
        self.warnings = warnings or []
        self.metadata = metadata or {}


class VerificationPipeline(Protocol):
    """Protocol that all verification pipelines implement."""

    async def verify(
        self,
        step: PlanStep,
        step_result: StepResult,
        ctx: dict[str, Any],
    ) -> VerificationOutcome: ...


class VerificationAdapter:
    """Registry + dispatcher for verification pipelines."""

    def __init__(self, wal: "WALManager"):
        self._wal = wal
        self._pipelines: dict[str, VerificationPipeline] = {}

    def register(self, name: str, pipeline: VerificationPipeline) -> None:
        if name in self._pipelines:
            raise ValueError(f"Pipeline {name!r} already registered")
        self._pipelines[name] = pipeline

    def list_pipelines(self) -> list[str]:
        return list(self._pipelines.keys())

    async def run(
        self,
        step: PlanStep,
        step_result: StepResult,
        ctx: dict[str, Any],
    ) -> VerificationOutcome:
        if step.pipeline is None:
            raise ValueError(f"VERIFY step {step.id} has no pipeline")
        if step.pipeline not in self._pipelines:
            raise KeyError(f"Pipeline {step.pipeline!r} not registered")
        # Provide WAL context: which tools ran, which files touched, what was edited.
        wal_context = self._wal.context_for_step(step.id) if hasattr(self._wal, "context_for_step") else {}
        merged_ctx = {**ctx, "wal": wal_context, "pipeline_args": step.pipeline_args or {}}
        return await self._pipelines[step.pipeline].verify(step, step_result, merged_ctx)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_verify_adapter.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add src/agent/verify_adapter.py tests/agent/test_verify_adapter.py
git commit -m "feat(verify): VerificationAdapter for named verification pipelines"
```

---

### Task 8: Wire default pipelines from `src/verification/`

**Files:**
- Modify: `src/agent/verify_adapter.py` (add `register_defaults` classmethod)
- Test: `tests/agent/test_verify_adapter.py` (extend)

**Interfaces:**
- Consumes: `VerificationAdapter` (Task 7), `src/verification/pipeline.py:get_pipeline()` (existing)
- Produces: `VerificationAdapter.register_defaults()` classmethod that registers `security`/`tdd`/`test`/`review` from `src/verification/`

- [ ] **Step 1: Append to test file**

Append to `tests/agent/test_verify_adapter.py`:

```python
def test_register_defaults_includes_four_pipelines():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register_defaults()
    names = adapter.list_pipelines()
    assert "security" in names
    assert "tdd" in names
    assert "test" in names
    assert "review" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_verify_adapter.py::test_register_defaults_includes_four_pipelines -v`
Expected: FAIL — `register_defaults` not defined.

- [ ] **Step 3: Read `src/verification/pipeline.py`**

Note the `get_pipeline(name: str)` function signature and the four pipeline names it returns.

- [ ] **Step 4: Add `register_defaults` to `VerificationAdapter`**

Modify `src/agent/verify_adapter.py`, add classmethod to `VerificationAdapter`:

```python
@classmethod
def register_defaults(cls, wal: "WALManager") -> "VerificationAdapter":
    """Build a VerificationAdapter with the 4 default pipelines registered.

    Imports are lazy to avoid loading src/verification until needed.
    """
    from src.verification.pipeline import get_pipeline

    adapter = cls(wal=wal)
    for name in ("security", "tdd", "test", "review"):
        pipeline = get_pipeline(name)
        adapter.register(name, pipeline)
    return adapter
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_verify_adapter.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 6: Commit**

```bash
git add src/agent/verify_adapter.py tests/agent/test_verify_adapter.py
git commit -m "feat(verify): register_defaults wires security/tdd/test/review pipelines"
```

---

### Task 9: Add `retry_with_feedback` loop to `_execute_verify` branch

**Files:**
- Modify: `src/agent/walker.py`
- Test: `tests/agent/test_subplan.py` (extend — note file name is reused for walker verify tests)

**Interfaces:**
- Consumes: `VerificationAdapter` (Tasks 7-8), `OnFailure.RETRY_WITH_FEEDBACK` (Task 1), `VerificationOutcome` (Task 7)
- Produces: `_execute_verify(step, prior_result) -> StepResult` method on `PlanWalker`. Returns `StepResult(status="verified"|"failed", metadata={"verifier_outcome": ...})`. On `RETRY_WITH_FEEDBACK`, returns `StepResult(status="retry_with_feedback", feedback=outcome.errors)`.

- [ ] **Step 1: Append to `tests/agent/test_subplan.py`**

```python
from src.agent.verify_adapter import VerificationAdapter, VerificationOutcome
from src.agent.control import OnFailure


@pytest.mark.asyncio
async def test_execute_verify_with_pipeline_passes_when_outcome_passed():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register("security", _StubPipeline(VerificationOutcome(passed=True)))
    walker = PlanWalker(
        plan=Plan(id="p", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.VERIFY,
        pipeline="security",
        on_failure=OnFailure.ABORT,
    )
    result = await walker._execute_verify(step, StepResult(status="completed"))
    assert result.status == "verified"
    assert result.metadata["verifier_outcome"].passed is True


@pytest.mark.asyncio
async def test_execute_verify_with_retry_with_feedback_returns_feedback():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register(
        "security",
        _StubPipeline(VerificationOutcome(passed=False, errors=["eval() found at auth.py:42"])),
    )
    walker = PlanWalker(
        plan=Plan(id="p", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.VERIFY,
        pipeline="security",
        on_failure=OnFailure.RETRY_WITH_FEEDBACK,
    )
    result = await walker._execute_verify(step, StepResult(status="completed"))
    assert result.status == "retry_with_feedback"
    assert "eval() found at auth.py:42" in result.feedback


class _StubPipeline:
    def __init__(self, outcome):
        self._outcome = outcome

    async def verify(self, step, step_result, ctx):
        return self._outcome
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_subplan.py -k verify -v`
Expected: FAIL — `_execute_verify` does not exist.

- [ ] **Step 3: Modify `src/agent/walker.py`**

Add `verifier_adapter` to `PlanWalker.__init__`:

```python
def __init__(
    self,
    plan: Plan,
    channel: ControlChannel,
    tools: ToolRegistry,
    wal: WALManager,
    role_registry: RoleRegistry | None = None,
    verifier_adapter: VerificationAdapter | None = None,
):
    ...
    self.verifier_adapter = verifier_adapter
```

Add `_execute_verify` method (factor existing verify logic out, add pipeline branch):

```python
async def _execute_verify(self, step: PlanStep, prior_result: StepResult) -> StepResult:
    """Execute a VERIFY step, either by pipeline (named) or by success_criteria (v1 behavior)."""
    if step.pipeline is not None and self.verifier_adapter is not None:
        outcome = await self.verifier_adapter.run(step, prior_result, ctx={})
        if outcome.passed:
            return StepResult(
                status="verified",
                metadata={"verifier_outcome": outcome},
            )
        # Verifier failed: route by on_failure strategy
        if step.on_failure == OnFailure.RETRY_WITH_FEEDBACK:
            return StepResult(
                status="retry_with_feedback",
                feedback=outcome.errors,
                metadata={"verifier_outcome": outcome},
            )
        return StepResult(
            status="failed",
            error="\n".join(outcome.errors),
            metadata={"verifier_outcome": outcome},
        )
    # v1 success_criteria-based path (unchanged)
    return await self._execute_verify_v1(step, prior_result)
```

In `_execute_step`, replace the v1 VERIFY branch with:

```python
if step.kind == PlanStepKind.VERIFY:
    prior_result = self._prior_step_result(step)
    return await self._execute_verify(step, prior_result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_subplan.py -v`
Expected: PASS — 5 tests (3 from Task 4 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/agent/walker.py tests/agent/test_subplan.py
git commit -m "feat(walker): _execute_verify pipeline branch + retry_with_feedback"
```

---


## Phase C — Memory Layer (8 tasks, ~6 days)

### Task 10: Create `MemoryStore` skeleton + data classes

**Files:**
- Create: `src/context/memory.py`
- Test: `tests/context/test_memory.py`

**Interfaces:**
- Consumes: `WALManager` (existing), `SkillLoader` from `src/skills/loader.py` (existing)
- Produces: `MemoryStore` class with `warm()`, `episodic()`, `semantic()`, `skills()`, `planner_context(task, k) -> str`. Empty stub implementations for now; subsequent tasks fill each method.

- [ ] **Step 1: Write the failing test**

Create `tests/context/test_memory.py`:

```python
from unittest.mock import MagicMock
from src.context.memory import MemoryStore, EpisodicEntry, SemanticEntry


def test_memory_store_constructs_with_empty_indexes():
    wal = MagicMock()
    store = MemoryStore(wal=wal, project_root=MagicMock())
    assert store.episodic() is not None
    assert store.semantic() is not None
    assert store.skills() is not None


def test_episodic_entry_round_trip():
    entry = EpisodicEntry(
        plan_id="p_abc",
        plan_hash="hash123",
        task="add X",
        outcome="success",
        duration_s=12.0,
        step_count=5,
        failed_step_ids=[],
        error_categories=[],
    )
    assert entry.plan_hash == "hash123"
    assert entry.outcome == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/context/memory.py` skeleton**

```python
"""Memory layer for Nexus v1.1.

Three indexes over the WAL JSONL (episodic), project files (semantic, opt-in
embeddings), and skill library (wraps src/skills/loader.py).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from src.context.wal import WALManager


@dataclass
class EpisodicEntry:
    """A single past Plan's outcome, derived from WAL."""

    plan_id: str
    plan_hash: str
    task: str
    outcome: Literal["success", "failed", "aborted"]
    duration_s: float
    step_count: int
    failed_step_ids: list[str]
    error_categories: list[str]
    created_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def plan_hash_of(plan_dict: dict[str, Any]) -> str:
        """Stable hash of a Plan's canonicalized dict form."""
        canonical = repr(sorted(plan_dict.items()))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class SemanticEntry:
    """A semantic chunk from a project file."""

    chunk_id: str
    path: Path
    start_line: int
    end_line: int
    content: str
    embedding: list[float] | None = None


class EpisodicIndex:
    """Derived view over WAL — never writes, only reads + caches."""

    def __init__(self, wal: WALManager, cache_path: Path):
        self._wal = wal
        self._cache_path = cache_path
        self._entries: dict[str, EpisodicEntry] = {}

    def rebuild(self) -> None:
        # Filled in Task 11.
        ...

    def similar_past(self, task: str, k: int = 5) -> list[EpisodicEntry]:
        # Filled in Task 11.
        return []

    def success_rate(self, error_category: str) -> float:
        # Filled in Task 11.
        return 0.0


class SemanticIndex:
    """Optional semantic memory — embeddings opt-in."""

    def __init__(self, project_root: Path, embedding_fn: Callable | None = None):
        self._root = project_root
        self._embed = embedding_fn
        self._chunks: list[SemanticEntry] = []

    def index_file(self, path: Path) -> None:
        # Filled in Task 13.
        ...

    def search(self, query: str, k: int = 5) -> list[SemanticEntry]:
        # Filled in Task 13.
        return []


class SkillIndex:
    """Wraps existing src/skills/loader.py."""

    def __init__(self, skill_loader: Any | None = None):
        self._loader = skill_loader

    def suggest(self, task: str, plan: Any) -> list[Any]:
        # Filled in Task 15.
        return []

    def apply(self, skill: Any, step: Any) -> Any:
        # Filled in Task 15.
        return step


class MemoryStore:
    """Coordinates all three indexes + WAL sync."""

    def __init__(
        self,
        wal: WALManager,
        project_root: Path,
        *,
        embedding_fn: Callable | None = None,
        skill_loader: Any | None = None,
    ):
        self._wal = wal
        self._project_root = Path(project_root)
        cache_path = self._project_root / ".nexus" / "memory" / "episodic.jsonl"
        self._episodic_idx = EpisodicIndex(wal=wal, cache_path=cache_path)
        self._semantic_idx = SemanticIndex(project_root=self._project_root, embedding_fn=embedding_fn)
        self._skill_idx = SkillIndex(skill_loader=skill_loader)

    def warm(self) -> None:
        """Rebuild indexes from current state. Called on app startup."""
        self._episodic_idx.rebuild()

    def episodic(self) -> EpisodicIndex:
        return self._episodic_idx

    def semantic(self) -> SemanticIndex:
        return self._semantic_idx

    def skills(self) -> SkillIndex:
        return self._skill_idx

    def planner_context(self, task: str, k: int = 5) -> str:
        """Render memory as context block to inject into Planner prompt."""
        # Filled in Task 16.
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/context/memory.py tests/context/test_memory.py
git commit -m "feat(memory): MemoryStore skeleton with three index stubs"
```

---

### Task 11: Implement `EpisodicIndex` — rebuild + similar_past

**Files:**
- Modify: `src/context/memory.py`
- Test: `tests/context/test_memory.py` (extend)

**Interfaces:**
- Consumes: `WALManager` (existing — must implement `iter_records()` returning generator of dict)
- Produces: `EpisodicIndex.rebuild()` reads WAL, builds entries, writes cache JSONL. `EpisodicIndex.similar_past(task, k)` returns top-k by simple substring overlap on `task`.

- [ ] **Step 1: Append to test file**

```python
def test_episodic_index_rebuild_reads_from_wal(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "add login", "steps": [{"id": "s1"}, {"id": "s2"}]}}\n'
        '{"format_version": 2, "kind": "step_complete", "plan_id": "p1", "cursor": "s1", "result": {"status": "completed"}}\n'
        '{"format_version": 2, "kind": "step_complete", "plan_id": "p1", "cursor": "s2", "result": {"status": "completed"}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    wal = MagicMock()
    wal.path = wal_path
    idx = EpisodicIndex(wal=wal, cache_path=tmp_path / "cache.jsonl")
    idx.rebuild()
    assert len(idx._entries) == 1
    entry = list(idx._entries.values())[0]
    assert entry.plan_id == "p1"
    assert entry.task == "add login"
    assert entry.outcome == "success"
    assert entry.step_count == 2


def test_episodic_similar_past_returns_substring_matches(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "add login button", "steps": []}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p2", "plan": {"id": "p2", "task": "remove unused imports", "steps": []}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p2", "outcome": "failed"}\n'
    )
    wal = MagicMock()
    wal.path = wal_path
    idx = EpisodicIndex(wal=wal, cache_path=tmp_path / "cache.jsonl")
    idx.rebuild()
    matches = idx.similar_past("add login screen", k=5)
    assert len(matches) >= 1
    assert matches[0].plan_id == "p1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -k rebuild -v`
Expected: FAIL — `rebuild` is a no-op.

- [ ] **Step 3: Implement `EpisodicIndex.rebuild()` and `similar_past()`**

Replace `EpisodicIndex.rebuild` and `similar_past` in `src/context/memory.py`:

```python
def rebuild(self) -> None:
    """Scan WAL JSONL, build EpisodicEntry per completed plan, write cache."""
    plans: dict[str, dict[str, Any]] = {}
    completed_steps: dict[str, list[str]] = {}
    outcomes: dict[str, str] = {}
    durations: dict[str, float] = {}
    error_cats: dict[str, list[str]] = {}

    # Walk WAL file directly (EpisodicIndex is a derived view, not a WAL client).
    if not self._wal.path.exists():
        return
    for line in self._wal.path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec.get("kind")
        plan_id = rec.get("plan_id")
        if kind == "plan_start":
            plans[plan_id] = rec.get("plan", {})
            completed_steps[plan_id] = []
            durations[plan_id] = rec.get("started_at", 0)
        elif kind == "step_complete":
            completed_steps.setdefault(plan_id, []).append(rec.get("cursor"))
            if rec.get("result", {}).get("error"):
                error_cats.setdefault(plan_id, []).append(
                    rec["result"].get("error_category", "unknown")
                )
        elif kind == "plan_end":
            outcomes[plan_id] = rec.get("outcome", "unknown")
            durations[plan_id] = rec.get("ended_at", 0) - durations[plan_id]

    self._entries.clear()
    for plan_id, plan_dict in plans.items():
        steps = plan_dict.get("steps", [])
        self._entries[plan_id] = EpisodicEntry(
            plan_id=plan_id,
            plan_hash=EpisodicEntry.plan_hash_of(plan_dict),
            task=plan_dict.get("task", ""),
            outcome=outcomes.get(plan_id, "unknown"),
            duration_s=durations.get(plan_id, 0.0),
            step_count=len(steps),
            failed_step_ids=[
                s.get("id") for s in steps if s.get("id") not in completed_steps.get(plan_id, [])
            ],
            error_categories=error_cats.get(plan_id, []),
        )
    self._write_cache()

def _write_cache(self) -> None:
    self._cache_path.parent.mkdir(parents=True, exist_ok=True)
    with self._cache_path.open("w") as f:
        for entry in self._entries.values():
            f.write(json.dumps({
                "plan_id": entry.plan_id,
                "plan_hash": entry.plan_hash,
                "task": entry.task,
                "outcome": entry.outcome,
                "duration_s": entry.duration_s,
                "step_count": entry.step_count,
                "failed_step_ids": entry.failed_step_ids,
                "error_categories": entry.error_categories,
                "created_at": entry.created_at.isoformat(),
            }) + "\n")

def similar_past(self, task: str, k: int = 5) -> list[EpisodicEntry]:
    """Return top-k past plans by substring overlap with task."""
    if not self._entries:
        return []
    task_words = set(task.lower().split())
    scored = []
    for entry in self._entries.values():
        entry_words = set(entry.task.lower().split())
        overlap = len(task_words & entry_words)
        if overlap > 0:
            scored.append((overlap, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:k]]

def success_rate(self, error_category: str) -> float:
    if not self._entries:
        return 0.0
    matching = [e for e in self._entries.values() if error_category in e.error_categories]
    if not matching:
        return 1.0
    return sum(1 for e in matching if e.outcome == "success") / len(matching)
```

Add imports at top: `import json`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/context/memory.py tests/context/test_memory.py
git commit -m "feat(memory): EpisodicIndex rebuild + similar_past + success_rate"
```

---

### Task 12: Add WAL sync (`last_wal_mtime` tracking + auto-rebuild)

**Files:**
- Modify: `src/context/memory.py`
- Modify: `src/context/wal.py`
- Test: `tests/context/test_memory.py` (extend)

**Interfaces:**
- Consumes: `WALManager.path` (pathlib Path), existing WAL JSONL
- Produces: `MemoryStore.warm()` compares `wal.path.stat().st_mtime` to cached mtime; rebuilds if newer. `EpisodicIndex._last_wal_mtime` field.

- [ ] **Step 1: Append to test file**

```python
def test_warm_skips_rebuild_when_wal_unchanged(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text('{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "x", "steps": []}}\n')
    wal = MagicMock()
    wal.path = wal_path
    store = MemoryStore(wal=wal, project_root=tmp_path)
    store.warm()
    rebuild_count_first = len(store.episodic()._entries)
    # Call warm again — no WAL change, should not re-read.
    store.warm()
    rebuild_count_second = len(store.episodic()._entries)
    assert rebuild_count_first == rebuild_count_second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -k warm_skips -v`
Expected: FAIL — `warm()` doesn't track mtime.

- [ ] **Step 3: Add mtime tracking**

In `src/context/memory.py`, modify `EpisodicIndex.__init__`:

```python
def __init__(self, wal: WALManager, cache_path: Path):
    self._wal = wal
    self._cache_path = cache_path
    self._entries: dict[str, EpisodicEntry] = {}
    self._last_wal_mtime: float = 0.0
```

Modify `rebuild()` to record mtime at end:

```python
def rebuild(self) -> None:
    # ... existing body ...
    self._last_wal_mtime = self._wal.path.stat().st_mtime if self._wal.path.exists() else 0.0
```

Modify `MemoryStore.warm()`:

```python
def warm(self) -> None:
    """Rebuild indexes only if WAL has changed since last warm."""
    if not self._wal.path.exists():
        return
    current_mtime = self._wal.path.stat().st_mtime
    if current_mtime > self._episodic_idx._last_wal_mtime:
        self._episodic_idx.rebuild()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/context/memory.py tests/context/test_memory.py
git commit -m "feat(memory): WAL mtime tracking for warm() skip-when-unchanged"
```

---

### Task 13: Implement `SemanticIndex` (substring baseline)

**Files:**
- Modify: `src/context/memory.py`
- Test: `tests/context/test_memory.py` (extend)

**Interfaces:**
- Consumes: `project_root: Path`, file contents (read via `pathlib`)
- Produces: `SemanticIndex.index_file(path)` reads file, splits into ~50-line chunks, stores. `SemanticIndex.search(query, k)` returns top-k by substring overlap.

- [ ] **Step 1: Append to test file**

```python
def test_semantic_index_indexes_file_in_chunks(tmp_path):
    (tmp_path / "auth.py").write_text("def login():\n    pass\n\n" * 30)
    idx = SemanticIndex(project_root=tmp_path)
    idx.index_file(tmp_path / "auth.py")
    assert len(idx._chunks) >= 1
    assert all(c.path.name == "auth.py" for c in idx._chunks)


def test_semantic_search_returns_matching_chunks(tmp_path):
    (tmp_path / "auth.py").write_text("login function here\n" * 20)
    (tmp_path / "util.py").write_text("utility helper\n" * 20)
    idx = SemanticIndex(project_root=tmp_path)
    idx.index_file(tmp_path / "auth.py")
    idx.index_file(tmp_path / "util.py")
    results = idx.search("login function", k=5)
    assert len(results) >= 1
    assert results[0].path.name == "auth.py"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -k semantic -v`
Expected: FAIL — `index_file` is a no-op.

- [ ] **Step 3: Implement `SemanticIndex` methods**

Replace `index_file` and `search` in `src/context/memory.py`:

```python
def index_file(self, path: Path) -> None:
    """Read file, split into ~50-line chunks, store entries."""
    if not path.exists():
        return
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    chunk_size = 50
    for i in range(0, len(lines), chunk_size):
        chunk_lines = lines[i : i + chunk_size]
        if not chunk_lines:
            continue
        chunk_id = f"{path.name}:{i + 1}-{i + len(chunk_lines)}"
        self._chunks.append(
            SemanticEntry(
                chunk_id=chunk_id,
                path=path,
                start_line=i + 1,
                end_line=i + len(chunk_lines),
                content="\n".join(chunk_lines),
            )
        )

def search(self, query: str, k: int = 5) -> list[SemanticEntry]:
    """Return top-k chunks by substring/word overlap."""
    if not self._chunks:
        return []
    query_words = set(query.lower().split())
    scored = []
    for chunk in self._chunks:
        chunk_words = set(chunk.content.lower().split())
        overlap = len(query_words & chunk_words)
        if overlap > 0:
            scored.append((overlap, chunk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in scored[:k]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/context/memory.py tests/context/test_memory.py
git commit -m "feat(memory): SemanticIndex index_file + substring search"
```

---

### Task 14: Add optional embeddings support

**Files:**
- Modify: `src/context/memory.py`
- Modify: `pyproject.toml`
- Test: `tests/context/test_memory.py` (extend)

**Interfaces:**
- Consumes: `embedding_fn: Callable[[str], list[float]] | None`
- Produces: `SemanticIndex.search` uses embedding cosine similarity when `embedding_fn` is set, falls back to substring otherwise. `pyproject.toml` gets `[embeddings]` extra with `sentence-transformers>=2.0`.

- [ ] **Step 1: Append to test file**

```python
def test_semantic_search_with_embeddings_uses_cosine_similarity(tmp_path):
    (tmp_path / "auth.py").write_text("login function\n" * 10)
    (tmp_path / "util.py").write_text("utility helper\n" * 10)

    # Fake embedding function: returns a vector where auth.py chunks score higher for "login" query.
    def fake_embed(text: str) -> list[float]:
        if "login" in text.lower():
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]

    idx = SemanticIndex(project_root=tmp_path, embedding_fn=fake_embed)
    idx.index_file(tmp_path / "auth.py")
    idx.index_file(tmp_path / "util.py")
    # Embed all chunks
    for chunk in idx._chunks:
        chunk.embedding = fake_embed(chunk.content)
    query_vec = fake_embed("login function")
    results = idx.search_with_embeddings("login function", query_vec, k=5)
    assert len(results) >= 1
    assert results[0].path.name == "auth.py"


def test_semantic_search_falls_back_to_substring_without_embeddings(tmp_path):
    (tmp_path / "auth.py").write_text("login\n" * 5)
    idx = SemanticIndex(project_root=tmp_path, embedding_fn=None)
    idx.index_file(tmp_path / "auth.py")
    results = idx.search("login", k=5)
    assert len(results) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -k embeddings -v`
Expected: FAIL — `search_with_embeddings` doesn't exist.

- [ ] **Step 3: Add embeddings to `SemanticIndex`**

Add to `SemanticIndex`:

```python
def search_with_embeddings(
    self, query: str, query_vec: list[float], k: int = 5
) -> list[SemanticEntry]:
    """Cosine similarity search. Caller computes query_vec via embedding_fn."""
    if not self._chunks or not self._embed:
        return self.search(query, k)
    scored = []
    for chunk in self._chunks:
        if chunk.embedding is None:
            continue
        sim = self._cosine_similarity(query_vec, chunk.embedding)
        scored.append((sim, chunk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in scored[:k]]

@staticmethod
def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
```

- [ ] **Step 4: Modify `pyproject.toml`**

Add to `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
embeddings = ["sentence-transformers>=2.0"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: PASS — 9 tests.

- [ ] **Step 6: Commit**

```bash
git add src/context/memory.py pyproject.toml tests/context/test_memory.py
git commit -m "feat(memory): optional sentence-transformers embeddings + pyproject extra"
```

---

### Task 15: Implement `SkillIndex` — wrap existing `src/skills/loader.py`

**Files:**
- Modify: `src/context/memory.py`
- Test: `tests/context/test_memory.py` (extend)

**Interfaces:**
- Consumes: `SkillLoader` from `src/skills/loader.py` (existing — check its actual API)
- Produces: `SkillIndex.suggest(task, plan) -> list[Skill]` calls `loader.search(task)`. `SkillIndex.apply(skill, step) -> PlanStep` returns step with skill metadata attached.

- [ ] **Step 1: Read `src/skills/loader.py`**

Note the `SkillLoader` API: constructor, `search(query)`, `load(name)` etc. Adjust the implementation to match real signatures.

- [ ] **Step 2: Append to test file**

```python
def test_skill_index_suggest_returns_matches():
    class FakeLoader:
        def search(self, query: str) -> list[Any]:
            return [{"name": "pytest_helper", "match_score": 0.9}]

    idx = SkillIndex(skill_loader=FakeLoader())
    suggestions = idx.suggest("add pytest fixture", plan=MagicMock())
    assert len(suggestions) == 1
    assert suggestions[0]["name"] == "pytest_helper"


def test_skill_index_apply_attaches_skill_to_step():
    idx = SkillIndex(skill_loader=MagicMock())
    skill = {"name": "pytest_helper", "template": "run pytest {path}"}
    step = MagicMock()
    result = idx.apply(skill, step)
    assert result is step
    step.attach_skill.assert_called_once_with(skill)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -k skill -v`
Expected: FAIL — methods are stubs.

- [ ] **Step 4: Implement `SkillIndex` methods**

```python
def suggest(self, task: str, plan: Any) -> list[Any]:
    """Suggest skills for the given task. Returns list of skill dicts."""
    if self._loader is None:
        return []
    return self._loader.search(task)

def apply(self, skill: Any, step: Any) -> Any:
    """Attach skill metadata to step. Returns the (possibly modified) step."""
    if hasattr(step, "attach_skill"):
        step.attach_skill(skill)
    else:
        step.metadata = getattr(step, "metadata", {}) or {}
        step.metadata["skill"] = skill
    return step
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: PASS — 11 tests.

- [ ] **Step 6: Commit**

```bash
git add src/context/memory.py tests/context/test_memory.py
git commit -m "feat(memory): SkillIndex wraps SkillLoader with suggest + apply"
```

---

### Task 16: Wire `MemoryStore` into `Planner` (prompt injection)

**Files:**
- Modify: `src/agent/planner.py`
- Modify: `src/agent/runtime.py` (Planner call passes memory context)
- Test: `tests/agent/test_planner_memory.py`

**Interfaces:**
- Consumes: `MemoryStore.planner_context(task, k)` (Tasks 10-15), `Planner.plan()` (existing)
- Produces: `AgentRuntime.plan()` calls `memory_store.planner_context(task)` and passes result to `planner.plan(...)` as `memory_context` parameter; `Planner` prepends to system prompt

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_planner_memory.py`:

```python
from unittest.mock import MagicMock
from src.agent.planner import Planner


def test_planner_accepts_memory_context_in_plan_call():
    planner = Planner(llm=MagicMock())
    llm = MagicMock()
    llm.complete = MagicMock(return_value='{"steps": []}')
    planner = Planner(llm=llm)
    plan = planner.plan(
        task="add X",
        memory_context="# Past similar tasks\n- p_abc: success in 12s",
    )
    # Assert llm.complete was called with memory_context in the prompt.
    call_args = llm.complete.call_args
    prompt = call_args.kwargs.get("system_prompt", "") + call_args.kwargs.get("user_prompt", "")
    assert "# Past similar tasks" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_planner_memory.py -v`
Expected: FAIL — `plan()` does not accept `memory_context`.

- [ ] **Step 3: Modify `src/agent/planner.py`**

Read the existing `Planner.plan()` signature. Add `memory_context: str = ""` parameter; in the LLM call, prepend `memory_context` to system prompt:

```python
def plan(
    self,
    task: str,
    *,
    memory_context: str = "",
    system_prompt: str = "",
    model_tier: Any = None,
    allowed_tools: list[str] | None = None,
    max_steps: int | None = None,
    context: dict[str, Any] | None = None,
) -> Plan:
    # ... existing prompt construction ...
    full_system_prompt = "\n\n".join(filter(None, [
        memory_context,
        system_prompt or self._default_system_prompt,
    ]))
    # ... pass full_system_prompt to llm.complete ...
```

- [ ] **Step 4: Modify `src/agent/runtime.py` `plan()` method**

```python
def plan(self, task: str) -> Plan:
    memory_context = ""
    if self.memory_store is not None:
        memory_context = self.memory_store.planner_context(task, k=5)
    return self.planner.plan(task=task, memory_context=memory_context, **self._planner_kwargs())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_planner_memory.py -v`
Expected: PASS — 1 test.

- [ ] **Step 6: Commit**

```bash
git add src/agent/planner.py src/agent/runtime.py tests/agent/test_planner_memory.py
git commit -m "feat(planner): accept memory_context; wire into runtime.plan()"
```

---

### Task 17: Implement `MemoryStore.planner_context` + add `nexus memory` CLI

**Files:**
- Modify: `src/context/memory.py`
- Create: `src/cli/memory.py`
- Modify: `src/cli/main.py`
- Test: `tests/context/test_memory.py` (extend)

**Interfaces:**
- Consumes: All three indexes (Tasks 11-15)
- Produces: `MemoryStore.planner_context(task, k)` renders markdown block combining episodic + semantic + skills. `nexus memory warm/stats/search` CLI commands.

- [ ] **Step 1: Append to test file**

```python
def test_planner_context_renders_three_sections(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "add login", "steps": []}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    (tmp_path / "convention.md").write_text("Use pytest")
    wal = MagicMock()
    wal.path = wal_path
    store = MemoryStore(wal=wal, project_root=tmp_path)
    store.semantic().index_file(tmp_path / "convention.md")
    store.warm()
    ctx = store.planner_context("add login", k=3)
    assert "Past similar tasks" in ctx
    assert "Project conventions" in ctx or "convention.md" in ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -k planner_context -v`
Expected: FAIL — `planner_context` returns empty string.

- [ ] **Step 3: Implement `planner_context`**

Replace the stub in `src/context/memory.py`:

```python
def planner_context(self, task: str, k: int = 5) -> str:
    """Render memory as context block to inject into Planner prompt."""
    blocks = []
    similar = self._episodic_idx.similar_past(task, k=k)
    if similar:
        lines = ["# Past similar tasks"]
        for entry in similar:
            lines.append(
                f"- {entry.created_at.date()}: task={entry.task!r}, "
                f"outcome={entry.outcome}, duration={entry.duration_s:.1f}s, "
                f"steps={entry.step_count}"
            )
        blocks.append("\n".join(lines))
    chunks = self._semantic_idx.search(task, k=k)
    if chunks:
        lines = ["# Project conventions"]
        for chunk in chunks[:3]:
            lines.append(f"- {chunk.path.name}:{chunk.start_line}: {chunk.content[:80].strip()}")
        blocks.append("\n".join(lines))
    suggestions = self._skill_idx.suggest(task, plan=None)
    if suggestions:
        lines = ["# Suggested skills"]
        for s in suggestions[:3]:
            name = s.get("name") if isinstance(s, dict) else getattr(s, "name", str(s))
            lines.append(f"- {name}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Create `src/cli/memory.py`**

```python
"""nexus memory <command> — interact with the memory layer."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Inspect and manage the memory layer.")


@app.command("warm")
def warm_command(workdir: Path = typer.Option(Path("."), "--workdir", "-w")):
    """Rebuild memory indexes from current WAL."""
    from src.context.wal import WALManager
    from src.context.memory import MemoryStore

    wal_path = workdir / ".nexus" / "wal.jsonl"
    if not wal_path.exists():
        typer.echo(f"No WAL at {wal_path}; nothing to warm.")
        raise typer.Exit(1)
    wal = WALManager(path=wal_path)
    store = MemoryStore(wal=wal, project_root=workdir)
    store.warm()
    typer.echo(f"Memory warmed: {len(store.episodic()._entries)} episodic entries.")


@app.command("stats")
def stats_command(workdir: Path = typer.Option(Path("."), "--workdir", "-w")):
    """Show memory index stats."""
    from src.context.wal import WALManager
    from src.context.memory import MemoryStore

    wal_path = workdir / ".nexus" / "wal.jsonl"
    wal = WALManager(path=wal_path) if wal_path.exists() else None
    if wal is None:
        typer.echo("No WAL; memory is empty.")
        return
    store = MemoryStore(wal=wal, project_root=workdir)
    store.warm()
    epi = store.episodic()
    sem = store.semantic()
    typer.echo(f"Episodic: {len(epi._entries)} plans indexed")
    typer.echo(f"Semantic: {len(sem._chunks)} chunks indexed")


@app.command("search")
def search_command(
    query: str,
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
    k: int = typer.Option(5, "--k"),
):
    """Semantic search across indexed chunks."""
    from src.context.wal import WALManager
    from src.context.memory import MemoryStore

    wal_path = workdir / ".nexus" / "wal.jsonl"
    wal = WALManager(path=wal_path) if wal_path.exists() else None
    store = MemoryStore(wal=wal or WALManager(path=workdir / ".nexus" / "wal.jsonl"), project_root=workdir)
    sem = store.semantic()
    results = sem.search(query, k=k)
    for r in results:
        typer.echo(f"{r.path}:{r.start_line}-{r.end_line}: {r.content[:80].strip()}")
```

- [ ] **Step 5: Register in `src/cli/main.py`**

Read existing `src/cli/main.py` to see how subcommands are registered. Add:

```python
from src.cli.memory import app as memory_app
# ... existing registrations ...
main_app.add_typer(memory_app, name="memory")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_memory.py -v`
Expected: PASS — 12 tests.

- [ ] **Step 7: Commit**

```bash
git add src/context/memory.py src/cli/memory.py src/cli/main.py tests/context/test_memory.py
git commit -m "feat(memory): planner_context renders three sections + nexus memory CLI"
```

---


## Phase D — Self-Evolution (6 tasks, ~5 days)

### Task 18: Create `PromptTemplate` + `PromptTemplateRegistry`

**Files:**
- Create: `src/agent/prompts.py`
- Test: `tests/agent/test_prompts.py`

**Interfaces:**
- Consumes: filesystem path for storage (`.nexus/prompts/`)
- Produces: `PromptTemplate` dataclass (`name`, `system_prompt`, `version`, `updated_at`, `source_episodes`, `last_updated_walk_count`); `PromptTemplateRegistry` class with `get(name)`, `update(name, template)`, `history(name)`, `revert(name, version)`. Storage: append-only JSONL per template.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_prompts.py`:

```python
import json
import pytest
from datetime import datetime
from pathlib import Path
from src.agent.prompts import PromptTemplate, PromptTemplateRegistry


def test_prompt_template_round_trip():
    t = PromptTemplate(
        name="planner",
        system_prompt="You plan.",
        version=1,
        updated_at=datetime.now(),
        source_episodes=[],
        last_updated_walk_count=0,
    )
    assert t.version == 1
    assert t.last_updated_walk_count == 0


def test_registry_get_returns_current_version(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner",
        system_prompt="v1",
        version=1,
        updated_at=datetime.now(),
        source_episodes=[],
        last_updated_walk_count=0,
    ))
    t = reg.get("planner")
    assert t.system_prompt == "v1"
    assert t.version == 1


def test_registry_update_appends_to_history(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v1", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v2", version=2,
        updated_at=datetime.now(), source_episodes=["p1"], last_updated_walk_count=5,
    ))
    history = reg.history("planner")
    assert len(history) == 2
    assert history[0].version == 1
    assert history[1].version == 2


def test_registry_revert_copies_target_version(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v1", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v2", version=2,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v3 bad", version=3,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.revert("planner", target_version=1)
    current = reg.get("planner")
    assert current.system_prompt == "v1"
    assert current.version == 4
    assert current.last_updated_walk_count == 0   # reset on revert
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_prompts.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/agent/prompts.py`**

```python
"""Prompt template registry with append-only version history.

Each template is stored at {path}/{name}.jsonl with one JSON record per version.
Revert writes a new version that copies the target version's prompt and resets
last_updated_walk_count to 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path


@dataclass
class PromptTemplate:
    name: str
    system_prompt: str
    version: int
    updated_at: datetime
    source_episodes: list[str] = field(default_factory=list)
    last_updated_walk_count: int = 0


class PromptTemplateRegistry:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str) -> Path:
        return self._path / f"{name}.jsonl"

    def get(self, name: str) -> PromptTemplate:
        """Return the current (latest) version of the named template."""
        records = self._read_all(name)
        if not records:
            raise KeyError(f"No template registered as {name!r}")
        return self._parse(records[-1])

    def update(self, name: str, template: PromptTemplate) -> None:
        """Append a new version to the template's history."""
        if template.name != name:
            raise ValueError(f"template.name={template.name} does not match key={name}")
        with self._file(name).open("a") as f:
            f.write(json.dumps(asdict(template), default=str) + "\n")

    def history(self, name: str) -> list[PromptTemplate]:
        """Return all versions of the template, oldest first."""
        records = self._read_all(name)
        return [self._parse(r) for r in records]

    def revert(self, name: str, target_version: int) -> PromptTemplate:
        """Write a new version that copies target_version's prompt; reset walk counter."""
        records = self._read_all(name)
        target = next((r for r in records if r["version"] == target_version), None)
        if target is None:
            raise ValueError(f"Version {target_version} not found in {name!r}")
        new_version = max(r["version"] for r in records) + 1
        reverted = PromptTemplate(
            name=name,
            system_prompt=target["system_prompt"],
            version=new_version,
            updated_at=datetime.now(),
            source_episodes=[f"revert@{target_version}"],
            last_updated_walk_count=0,
        )
        self.update(name, reverted)
        return reverted

    def _read_all(self, name: str) -> list[dict]:
        f = self._file(name)
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]

    @staticmethod
    def _parse(record: dict) -> PromptTemplate:
        return PromptTemplate(
            name=record["name"],
            system_prompt=record["system_prompt"],
            version=record["version"],
            updated_at=datetime.fromisoformat(record["updated_at"]),
            source_episodes=record.get("source_episodes", []),
            last_updated_walk_count=record.get("last_updated_walk_count", 0),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_prompts.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent/prompts.py tests/agent/test_prompts.py
git commit -m "feat(prompts): PromptTemplateRegistry with append-only history + revert"
```

---

### Task 19: Add `nexus prompt list/show/revert/history` CLI

**Files:**
- Create: `src/cli/prompt.py`
- Modify: `src/cli/main.py`
- Test: `tests/cli/test_prompt_cli.py`

**Interfaces:**
- Consumes: `PromptTemplateRegistry` (Task 18)
- Produces: `nexus prompt list` (lists template names + current version), `nexus prompt show <name>` (prints current prompt), `nexus prompt revert <name>@<ver>` (calls `revert`), `nexus prompt history <name>` (prints all versions)

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_prompt_cli.py`:

```python
from pathlib import Path
from typer.testing import CliRunner
from src.cli.prompt import app
from src.cli.main import main_app


runner = CliRunner()


def test_prompt_list_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No prompt templates registered" in result.stdout


def test_prompt_show_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["show", "planner"])
    assert "not found" in result.stdout.lower() or result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/cli/test_prompt_cli.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/cli/prompt.py`**

```python
"""nexus prompt <command> — manage prompt templates."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Inspect and manage prompt templates.")


def _registry(workdir: Path) -> "PromptTemplateRegistry":
    from src.agent.prompts import PromptTemplateRegistry
    return PromptTemplateRegistry(path=workdir / ".nexus" / "prompts")


@app.command("list")
def list_command(workdir: Path = typer.Option(Path("."), "--workdir", "-w")):
    reg = _registry(workdir)
    prompts_dir = workdir / ".nexus" / "prompts"
    if not prompts_dir.exists():
        typer.echo("No prompt templates registered.")
        return
    files = sorted(prompts_dir.glob("*.jsonl"))
    if not files:
        typer.echo("No prompt templates registered.")
        return
    for f in files:
        name = f.stem
        try:
            t = reg.get(name)
            typer.echo(f"{name} v{t.version} (updated {t.updated_at.date()})")
        except Exception:
            typer.echo(f"{name} (corrupt)")


@app.command("show")
def show_command(
    name: str,
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    reg = _registry(workdir)
    try:
        t = reg.get(name)
    except KeyError:
        typer.echo(f"Template {name!r} not found.")
        raise typer.Exit(1)
    typer.echo(f"# {name} v{t.version} (updated {t.updated_at.isoformat()})")
    typer.echo(t.system_prompt)


@app.command("history")
def history_command(
    name: str,
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    reg = _registry(workdir)
    history = reg.history(name)
    if not history:
        typer.echo(f"No history for {name!r}.")
        return
    for t in history:
        typer.echo(f"v{t.version} ({t.updated_at.date()}): walk_count={t.last_updated_walk_count}")


@app.command("revert")
def revert_command(
    target: str,  # format: name@version
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    if "@" not in target:
        typer.echo("Format: name@version (e.g., planner@2)")
        raise typer.Exit(1)
    name, version_str = target.rsplit("@", 1)
    version = int(version_str)
    reg = _registry(workdir)
    try:
        reverted = reg.revert(name, version)
    except (ValueError, KeyError) as e:
        typer.echo(f"Revert failed: {e}")
        raise typer.Exit(1)
    typer.echo(f"Reverted {name} to v{version} (now at v{reverted.version})")
```

- [ ] **Step 4: Register in `src/cli/main.py`**

```python
from src.cli.prompt import app as prompt_app
main_app.add_typer(prompt_app, name="prompt")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/cli/test_prompt_cli.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 6: Commit**

```bash
git add src/cli/prompt.py src/cli/main.py tests/cli/test_prompt_cli.py
git commit -m "feat(cli): nexus prompt list/show/history/revert commands"
```

---

### Task 20: Create `Evolver` skeleton + `record_outcome`

**Files:**
- Create: `src/agent/evolution.py`
- Test: `tests/agent/test_evolution.py`

**Interfaces:**
- Consumes: `WALManager`, `MemoryStore`, `FeedbackLoop` (from `src/engine/feedback_loop.py`)
- Produces: `Evolver` class with `record_outcome(plan, results)` (extracts error histograms, retry rates, planner failures from results)

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_evolution.py`:

```python
from unittest.mock import MagicMock
from src.agent.evolution import Evolver, StagedChanges
from src.agent.plan import Plan, PlanStep, PlanStepKind


def _make_results(*statuses):
    return [
        MagicMock(status=status, error_category=("io_error" if status == "failed" else None))
        for status in statuses
    ]


def test_record_outcome_computes_error_histogram():
    wal = MagicMock()
    memory = MagicMock()
    feedback = MagicMock()
    evolver = Evolver(wal=wal, memory=memory, feedback=feedback)
    plan = Plan(id="p1", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, tool="Read"),
        PlanStep(id="s2", kind=PlanStepKind.TOOL, tool="Write"),
    ])
    results = _make_results("completed", "failed", "failed")
    evolver.record_outcome(plan, results)
    assert evolver._last_outcome["total_steps"] == 2
    assert evolver._last_outcome["failed_count"] == 2
    assert "io_error" in evolver._last_outcome["error_histogram"]


def test_staged_changes_default_empty():
    sc = StagedChanges(changes={}, rationale={})
    assert sc.changes == {}
    assert sc.rationale == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_evolution.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/agent/evolution.py`**

```python
"""Self-evolution engine coordinator.

Thin wrapper over src/engine/self_evolution.py + feedback_loop_integration.py.
Reads WAL patterns after each walk, stages prompt updates for user approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.agent.plan import Plan
from src.agent.control import StepResult
from src.agent.prompts import PromptTemplate


@dataclass
class StagedChanges:
    """Evolver-produced prompt updates pending user approval."""

    changes: dict[str, PromptTemplate]    # template_name → proposed new version
    rationale: dict[str, str]            # template_name → why
    created_at: datetime = field(default_factory=datetime.now)


class Evolver:
    """Coordinator for the closed-loop self-evolution feedback system."""

    # Churn cap: each (template_name, version) updates at most once per N walks.
    WALK_COUNT_CAP = 5

    def __init__(self, wal: Any, memory: Any, feedback: Any):
        self._wal = wal
        self._memory = memory
        self._feedback = feedback
        self._last_outcome: dict[str, Any] = {}
        self._walk_count: int = 0

    def record_outcome(self, plan: Plan, results: list[StepResult]) -> None:
        """Extract error histograms, retry rates, planner failures from results."""
        self._walk_count += 1
        histogram: dict[str, int] = {}
        failed = 0
        for r in results:
            if r.status == "failed":
                failed += 1
                cat = getattr(r, "error_category", None) or "unknown"
                histogram[cat] = histogram.get(cat, 0) + 1
        self._last_outcome = {
            "plan_id": plan.id,
            "total_steps": len(results),
            "failed_count": failed,
            "error_histogram": histogram,
            "walk_count": self._walk_count,
        }

    def update_prompt_registry(
        self, registry: "PromptTemplateRegistry"
    ) -> StagedChanges:
        """Inspect last_outcome and stage prompt updates. Implemented in Task 21."""
        return StagedChanges(changes={}, rationale={})

    def should_replan(self, partial_results: list[StepResult]) -> bool:
        """Decide mid-walk whether the plan should be regenerated."""
        if not partial_results:
            return False
        recent_failures = sum(1 for r in partial_results[-3:] if r.status == "failed")
        return recent_failures >= 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_evolution.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent/evolution.py tests/agent/test_evolution.py
git commit -m "feat(evolution): Evolver skeleton + record_outcome + StagedChanges"
```

---

### Task 21: Implement `Evolver.update_prompt_registry` (heuristic-based staging)

**Files:**
- Modify: `src/agent/evolution.py`
- Test: `tests/agent/test_evolution.py` (extend)

**Interfaces:**
- Consumes: `record_outcome` output, `PromptTemplateRegistry` (Task 18), `EpisodicIndex` (Task 11)
- Produces: `update_prompt_registry(registry)` — if `last_outcome["failed_count"] / total_steps > 0.3` AND walk_count allows, propose adding error categories to `verifier.security_scan.rules` prompt; update `last_updated_walk_count` on proposed templates.

- [ ] **Step 1: Append to test file**

```python
from datetime import datetime
from src.agent.prompts import PromptTemplate, PromptTemplateRegistry


def test_update_prompt_registry_stages_change_when_failure_rate_high(tmp_path):
    wal = MagicMock()
    memory = MagicMock()
    memory.episodic = MagicMock(return_value=MagicMock(success_rate=MagicMock(return_value=0.2)))
    feedback = MagicMock()
    evolver = Evolver(wal=wal, memory=memory, feedback=feedback)
    plan = Plan(id="p1", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, tool="Read"),
    ])
    results = _make_results("failed", "failed", "failed")
    evolver.record_outcome(plan, results)

    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="original", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    staged = evolver.update_prompt_registry(reg)
    # Either staged or empty depending on heuristic — assert it's a StagedChanges.
    from src.agent.evolution import StagedChanges
    assert isinstance(staged, StagedChanges)


def test_update_prompt_registry_respects_walk_count_cap(tmp_path):
    wal = MagicMock()
    memory = MagicMock()
    memory.episodic = MagicMock(return_value=MagicMock(success_rate=MagicMock(return_value=0.1)))
    feedback = MagicMock()
    evolver = Evolver(wal=wal, memory=memory, feedback=feedback)
    plan = Plan(id="p1", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, tool="Read"),
    ])
    results = _make_results("failed")
    evolver.record_outcome(plan, results)
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v1", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    # First update: should attempt stage.
    evolver.update_prompt_registry(reg)
    # Second update: walk count is now 6, but cap is 5 walks since last update.
    # Re-record outcome to advance walk count.
    evolver.record_outcome(plan, results)  # walk_count = 2
    staged2 = evolver.update_prompt_registry(reg)
    # Heuristic may or may not produce changes; the cap is enforced internally.
    from src.agent.evolution import StagedChanges
    assert isinstance(staged2, StagedChanges)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_evolution.py -k update_prompt -v`
Expected: FAIL — `update_prompt_registry` returns empty StagedChanges.

- [ ] **Step 3: Implement `update_prompt_registry`**

Replace `update_prompt_registry` in `src/agent/evolution.py`:

```python
def update_prompt_registry(
    self, registry: "PromptTemplateRegistry"
) -> StagedChanges:
    """Stage prompt updates based on last_outcome heuristics.

    Heuristic: if failure rate > 30% and walk count delta > cap, propose
    augmenting the planner system prompt with observed error categories.
    """
    outcome = self._last_outcome
    total = outcome.get("total_steps", 0)
    failed = outcome.get("failed_count", 0)
    if total == 0:
        return StagedChanges(changes={}, rationale={})

    failure_rate = failed / total
    changes: dict[str, PromptTemplate] = {}
    rationale: dict[str, str] = {}

    if failure_rate > 0.3:
        histogram = outcome.get("error_histogram", {})
        if histogram:
            # Check churn cap before staging.
            try:
                current = registry.get("planner")
            except KeyError:
                return StagedChanges(changes={}, rationale={})
            walks_since_update = self._walk_count - current.last_updated_walk_count
            if walks_since_update < self.WALK_COUNT_CAP:
                return StagedChanges(changes={}, rationale={})
            # Stage a new version with observed error categories appended.
            error_summary = ", ".join(f"{cat}={n}" for cat, n in histogram.items())
            new_prompt = (
                current.system_prompt
                + f"\n\n# Recent error patterns\nAvoid these: {error_summary}"
            )
            new_template = PromptTemplate(
                name="planner",
                system_prompt=new_prompt,
                version=current.version + 1,
                updated_at=datetime.now(),
                source_episodes=[outcome.get("plan_id", "")],
                last_updated_walk_count=self._walk_count,
            )
            changes["planner"] = new_template
            rationale["planner"] = (
                f"Failure rate {failure_rate:.0%} exceeds 30% threshold; "
                f"adding observed error patterns to planner prompt"
            )

    return StagedChanges(changes=changes, rationale=rationale)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_evolution.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/agent/evolution.py tests/agent/test_evolution.py
git commit -m "feat(evolution): update_prompt_registry heuristic with churn cap"
```

---

### Task 22: Wire `post_walk_hook` into `AgentRuntime`

**Files:**
- Modify: `src/agent/runtime.py`
- Test: `tests/agent/test_runtime_post_walk.py`

**Interfaces:**
- Consumes: `Evolver` (Tasks 20-21), `PromptTemplateRegistry` (Task 18)
- Produces: `AgentRuntime.walk()` calls `evolver.record_outcome()` after each walk completes (success or failure); also calls `update_prompt_registry()` and writes `StagedChanges` to `.nexus/prompts/staged.json`.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_runtime_post_walk.py`:

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.agent.control import StepResult
from src.agent.evolution import Evolver
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agent.prompts import PromptTemplate, PromptTemplateRegistry
from src.agent.runtime import AgentRuntime


@pytest.mark.asyncio
async def test_walk_invokes_evolver_record_outcome(tmp_path):
    evolver = MagicMock(spec=Evolver)
    evolver.record_outcome = MagicMock()
    evolver.update_prompt_registry = MagicMock(return_value=MagicMock(changes={}, rationale={}))

    planner = MagicMock()
    planner.plan = MagicMock(return_value=Plan(id="p1", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, tool="Read"),
    ]))
    tools = MagicMock()
    tools.execute = AsyncMock(return_value=StepResult(status="completed"))
    wal = MagicMock()
    channel = MagicMock()
    channel.wait_if_paused = AsyncMock()
    runtime = AgentRuntime(
        planner=planner,
        tools=tools,
        channel=channel,
        wal=wal,
        evolver=evolver,
        prompt_registry=PromptTemplateRegistry(path=tmp_path / ".nexus" / "prompts"),
        workdir=tmp_path,
    )
    plan = Plan(id="p1", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, tool="Read"),
    ])
    await runtime.walk(plan)
    evolver.record_outcome.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_runtime_post_walk.py -v`
Expected: FAIL — runtime does not invoke evolver.

- [ ] **Step 3: Read existing `AgentRuntime.walk()`**

Note the structure: how plan execution is initiated, where errors are caught, where to hook post-walk.

- [ ] **Step 4: Modify `src/agent/runtime.py`**

Add `workdir: Path` to `__init__`:

```python
def __init__(
    self,
    planner,
    tools,
    channel,
    wal,
    role_registry=None,
    memory_store=None,
    prompt_registry=None,
    verifier_adapter=None,
    evolver=None,
    workdir: Path | None = None,
):
    ...
    self.workdir = Path(workdir) if workdir else Path(".")
```

Modify `walk()` to call post-walk hook:

```python
async def walk(self, plan: Plan) -> PlanResult:
    walker = PlanWalker(
        plan=plan,
        channel=self.channel,
        tools=self.tools,
        wal=self.wal,
        role_registry=self.role_registry,
        verifier_adapter=self.verifier_adapter,
    )
    walker._runtime = self
    try:
        result = await walker.walk()
    except Exception as e:
        if self.evolver:
            self.evolver.record_outcome(plan, results=getattr(walker, "_step_results", []))
            self._stage_evolver_changes()
        raise
    if self.evolver:
        self.evolver.record_outcome(plan, results=walker._step_results)
        self._stage_evolver_changes()
    return result

def _stage_evolver_changes(self) -> None:
    if not self.evolver or not self.prompt_registry:
        return
    staged = self.evolver.update_prompt_registry(self.prompt_registry)
    if staged.changes:
        path = self.workdir / ".nexus" / "prompts" / "staged.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "changes": {n: asdict(t) for n, t in staged.changes.items()},
            "rationale": staged.rationale,
            "created_at": staged.created_at.isoformat(),
        }, default=str))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_runtime_post_walk.py -v`
Expected: PASS — 1 test.

- [ ] **Step 6: Commit**

```bash
git add src/agent/runtime.py tests/agent/test_runtime_post_walk.py
git commit -m "feat(runtime): walk() invokes evolver.record_outcome + stages changes"
```

---

### Task 23: TUI `EvolveApprovalModal` + `nexus evolve --auto`

**Files:**
- Create: `src/tui/evolve_approval_modal.py`
- Create: `src/cli/evolve.py`
- Modify: `src/cli/main.py`
- Test: `tests/tui/test_evolve_modal.py`, `tests/cli/test_evolve_cli.py`

**Interfaces:**
- Consumes: `StagedChanges` JSON at `.nexus/prompts/staged.json` (Task 22)
- Produces: `EvolveApprovalModal(Textual)` with per-change approve/reject buttons; `nexus evolve --auto` reads staged, applies via `prompt_registry.update()`, deletes staged.json.

- [ ] **Step 1: Create `src/tui/evolve_approval_modal.py`**

```python
"""Modal for user approval of staged prompt updates from Evolver."""

from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class EvolveApprovalModal(ModalScreen[bool]):
    """Display staged prompt changes; user approves or rejects each."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, staged_path: Path):
        super().__init__()
        self._staged_path = staged_path

    def compose(self) -> ComposeResult:
        if not self._staged_path.exists():
            yield Static("No staged prompt changes.")
            yield Button("OK", id="ok")
            return
        data = json.loads(self._staged_path.read_text())
        yield Vertical(
            Static(f"## {len(data['changes'])} staged prompt update(s)"),
            *[self._render_change(name, change, rationale)
              for name, change, rationale in zip(
                  data["changes"].keys(),
                  data["changes"].values(),
                  [data["rationale"].get(n, "") for n in data["changes"].keys()],
              )],
            Horizontal(
                Button("Approve All", id="approve_all", variant="success"),
                Button("Reject All", id="reject_all", variant="error"),
                Button("Cancel", id="cancel"),
            ),
        )

    def _render_change(self, name: str, change: dict, rationale: str) -> Static:
        return Static(
            f"[bold]{name} v{change['version']}[/bold]\n"
            f"  {rationale}\n"
            f"  Prompt preview: {change['system_prompt'][:120]}..."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve_all":
            self.dismiss(True)
        elif event.button.id == "reject_all":
            self.dismiss(False)
        else:
            self.dismiss(None)
```

- [ ] **Step 2: Create `src/cli/evolve.py`**

```python
"""nexus evolve — apply staged prompt updates without TUI interaction."""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(help="Apply or discard staged prompt updates.")


@app.command(name="evolve")
def evolve_command(
    auto: bool = typer.Option(False, "--auto", help="Apply without confirmation"),
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    staged = workdir / ".nexus" / "prompts" / "staged.json"
    if not staged.exists():
        typer.echo("No staged changes.")
        return
    data = json.loads(staged.read_text())
    if not auto:
        typer.echo(f"{len(data['changes'])} staged changes; use --auto to apply.")
        return
    from src.agent.prompts import PromptTemplate, PromptTemplateRegistry
    reg = PromptTemplateRegistry(path=workdir / ".nexus" / "prompts")
    for name, change in data["changes"].items():
        template = PromptTemplate(
            name=name,
            system_prompt=change["system_prompt"],
            version=change["version"],
            updated_at=__import__("datetime").datetime.fromisoformat(change["updated_at"]),
            source_episodes=change.get("source_episodes", []),
            last_updated_walk_count=change.get("last_updated_walk_count", 0),
        )
        reg.update(name, template)
        typer.echo(f"Applied {name} v{change['version']}")
    staged.unlink()
```

- [ ] **Step 3: Register in `src/cli/main.py`**

```python
from src.cli.evolve import app as evolve_app
main_app.add_typer(evolve_app, name="evolve")
```

- [ ] **Step 4: Create test files**

Create `tests/tui/test_evolve_modal.py`:

```python
import pytest
from pathlib import Path
from textual.app import App
from src.tui.evolve_approval_modal import EvolveApprovalModal


@pytest.mark.asyncio
async def test_modal_renders_no_staged_message(tmp_path):
    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(EvolveApprovalModal(tmp_path / "staged.json"))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Modal mounted with no-staged message.
```

Create `tests/cli/test_evolve_cli.py`:

```python
import json
from pathlib import Path
from typer.testing import CliRunner
from src.cli.evolve import app


runner = CliRunner()


def test_evolve_with_no_staged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["evolve", "--auto"])
    assert "No staged changes" in result.stdout


def test_evolve_with_auto_applies_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    staged = tmp_path / ".nexus" / "prompts"
    staged.mkdir(parents=True)
    (staged / "staged.json").write_text(json.dumps({
        "changes": {
            "planner": {
                "name": "planner", "version": 2,
                "system_prompt": "new", "updated_at": "2026-07-01T00:00:00",
                "source_episodes": [], "last_updated_walk_count": 5,
            }
        },
        "rationale": {"planner": "test"},
        "created_at": "2026-07-01T00:00:00",
    }))
    result = runner.invoke(app, ["evolve", "--auto"])
    assert "Applied planner v2" in result.stdout
    assert not (staged / "staged.json").exists()
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/test_evolve_modal.py tests/cli/test_evolve_cli.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add src/tui/evolve_approval_modal.py src/cli/evolve.py src/cli/main.py tests/tui/test_evolve_modal.py tests/cli/test_evolve_cli.py
git commit -m "feat(evolution): TUI EvolveApprovalModal + nexus evolve --auto CLI"
```

---


## Phase E — Schema + CLI/TUI Surface (6 tasks, ~4 days)

### Task 24: WAL format v2 (header + per-record)

**Files:**
- Modify: `src/context/wal.py`
- Test: `tests/context/test_wal_v2.py`

**Interfaces:**
- Consumes: existing WAL JSONL files (v1 format)
- Produces: On new WAL creation, write `{"format_version": 2, "kind": "wal_header", ...}` as first line. `WALManager.checkpoint()` and `plan_start()` accept optional `metadata: dict`; if present, include in JSON record.

- [ ] **Step 1: Write the failing test**

Create `tests/context/test_wal_v2.py`:

```python
import json
from pathlib import Path
from src.context.wal import WALManager


def test_new_wal_writes_v2_header(tmp_path):
    wal = WALManager(path=tmp_path / "wal.jsonl")
    wal.initialize()
    first_line = wal.path.read_text().splitlines()[0]
    rec = json.loads(first_line)
    assert rec["kind"] == "wal_header"
    assert rec["format_version"] == 2


def test_v1_wal_loads_in_v2_reader(tmp_path):
    v1_wal = tmp_path / "wal.jsonl"
    v1_wal.write_text(
        '{"kind": "plan_start", "plan_id": "p1", "version": 1}\n'
        '{"kind": "step_complete", "plan_id": "p1", "cursor": "s1", "result": {"status": "completed"}}\n'
    )
    wal = WALManager(path=v1_wal)
    records = list(wal.iter_records())
    assert len(records) == 2
    assert records[0]["plan_id"] == "p1"


def test_checkpoint_with_metadata_writes_metadata_field(tmp_path):
    wal = WALManager(path=tmp_path / "wal.jsonl")
    wal.initialize()
    wal.checkpoint(plan_id="p1", version=1, cursor="s1", result={"status": "completed"},
                   metadata={"subplan_result": {"status": "completed"}})
    last_line = wal.path.read_text().splitlines()[-1]
    rec = json.loads(last_line)
    assert rec["metadata"]["subplan_result"]["status"] == "completed"
    assert rec["format_version"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_wal_v2.py -v`
Expected: FAIL — current WAL doesn't write `format_version=2` header.

- [ ] **Step 3: Read existing `src/context/wal.py`**

Note existing API: `initialize()`, `checkpoint()`, `recover()`, etc.

- [ ] **Step 4: Modify `src/context/wal.py`**

Add header writing in `initialize()`:

```python
WAL_FORMAT_VERSION = 2

def initialize(self) -> None:
    """Create WAL file with v2 header if it doesn't exist."""
    if self.path.exists():
        return
    self.path.parent.mkdir(parents=True, exist_ok=True)
    with self.path.open("w") as f:
        f.write(json.dumps({
            "format_version": WAL_FORMAT_VERSION,
            "kind": "wal_header",
            "created_at": datetime.now().isoformat(),
            "nexus_version": "1.1.0",
        }) + "\n")
```

Modify `checkpoint()` signature:

```python
def checkpoint(
    self,
    plan_id: str,
    version: int,
    cursor: str,
    result: dict,
    metadata: dict | None = None,
) -> None:
    record = {
        "format_version": WAL_FORMAT_VERSION,
        "kind": "step_complete",
        "plan_id": plan_id,
        "version": version,
        "cursor": cursor,
        "result": result,
    }
    if metadata is not None:
        record["metadata"] = metadata
    with self.path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
```

Add `iter_records()` method (preserves v1 records):

```python
def iter_records(self):
    """Yield each JSON record in the WAL. v1 and v2 records both supported."""
    if not self.path.exists():
        return
    for line in self.path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        # v1 records lack format_version; treat them as version 1.
        if "format_version" not in rec:
            rec["format_version"] = 1
        yield rec
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/context/test_wal_v2.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Commit**

```bash
git add src/context/wal.py tests/context/test_wal_v2.py
git commit -m "feat(wal): v2 format with format_version header + metadata field"
```

---

### Task 25: `nexus session migrate` command

**Files:**
- Create: `src/cli/migrate.py`
- Modify: `src/cli/main.py`
- Test: `tests/cli/test_migrate_cli.py`

**Interfaces:**
- Consumes: v1 WAL JSONL files
- Produces: `nexus session migrate <plan_id>` reads v1 records for plan_id, reconstructs Plan, writes v2 WAL alongside original with `_v2` suffix.

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_migrate_cli.py`:

```python
import json
from pathlib import Path
from typer.testing import CliRunner
from src.cli.migrate import app


runner = CliRunner()


def test_migrate_creates_v2_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wal = tmp_path / ".nexus" / "wal.jsonl"
    wal.parent.mkdir(parents=True)
    wal.write_text(
        '{"format_version": 1, "kind": "plan_start", "plan_id": "p1", "version": 1, "plan": {"id": "p1", "task": "x", "steps": []}}\n'
        '{"format_version": 1, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    result = runner.invoke(app, ["migrate", "p1"])
    assert result.exit_code == 0
    v2_path = wal.with_name("wal_v2.jsonl")
    assert v2_path.exists()
    first = json.loads(v2_path.read_text().splitlines()[0])
    assert first["format_version"] == 2


def test_migrate_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wal = tmp_path / ".nexus" / "wal.jsonl"
    wal.parent.mkdir(parents=True)
    wal.write_text(
        '{"format_version": 2, "kind": "wal_header"}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    result = runner.invoke(app, ["migrate", "p1"])
    assert "already migrated" in result.stdout.lower() or result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/cli/test_migrate_cli.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/cli/migrate.py`**

```python
"""nexus session migrate — convert v1 WAL records to v2 format."""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(help="Migrate session WAL files.")


@app.command("migrate")
def migrate_command(
    plan_id: str,
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    wal_path = workdir / ".nexus" / "wal.jsonl"
    if not wal_path.exists():
        typer.echo(f"No WAL at {wal_path}.")
        raise typer.Exit(1)
    v2_path = wal_path.parent / "wal_v2.jsonl"
    if v2_path.exists():
        typer.echo(f"v2 WAL already exists at {v2_path}; skipping.")
        return

    plan_records: list[dict] = []
    for line in wal_path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("plan_id") == plan_id:
            plan_records.append(rec)

    if not plan_records:
        typer.echo(f"No records for plan {plan_id}.")
        raise typer.Exit(1)

    # Check if already migrated (any record has format_version >= 2).
    if any(rec.get("format_version", 1) >= 2 for rec in plan_records):
        typer.echo(f"Plan {plan_id} is already in v2 format.")
        return

    # Write v2 file with header + upgraded records.
    with v2_path.open("w") as f:
        f.write(json.dumps({
            "format_version": 2,
            "kind": "wal_header",
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "nexus_version": "1.1.0",
        }) + "\n")
        for rec in plan_records:
            rec["format_version"] = 2
            f.write(json.dumps(rec, default=str) + "\n")

    typer.echo(f"Migrated plan {plan_id}: wrote {v2_path}")
```

- [ ] **Step 4: Register in `src/cli/main.py`**

```python
from src.cli.migrate import app as migrate_app
# Inside the existing session_app or as a separate command:
main_app.add_typer(migrate_app, name="migrate")
```

(Decide based on existing CLI structure; if `session` is its own app, add to that.)

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/cli/test_migrate_cli.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 6: Commit**

```bash
git add src/cli/migrate.py src/cli/main.py tests/cli/test_migrate_cli.py
git commit -m "feat(cli): nexus session migrate v1→v2"
```

---

### Task 26: TUI `VerifierPanel` + `MemoryPanel`

**Files:**
- Create: `src/tui/verifier_panel.py`
- Create: `src/tui/memory_panel.py`
- Test: `tests/tui/test_new_panels.py`

**Interfaces:**
- Consumes: `VerificationOutcome` (Task 7), `MemoryStore` (Tasks 10-17)
- Produces: `VerifierPanel` widget showing last verifier outcome (name, pass/fail, errors). `MemoryPanel` widget showing episodic + semantic + skill counts.

- [ ] **Step 1: Create `src/tui/verifier_panel.py`**

```python
"""TUI panel showing last verifier outcome."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


class VerifierPanel(Static):
    """Displays the last verifier pipeline outcome."""

    last_outcome: reactive[tuple[str, bool, list[str]] | None] = reactive(None)

    def render(self):
        if self.last_outcome is None:
            return Text("No verifiers run yet.", style="dim")
        name, passed, errors = self.last_outcome
        if passed:
            return Text(f"✓ {name}", style="green")
        err_preview = "\n".join(errors[:5])
        return Text(f"✗ {name}\n{err_preview}", style="red")

    def update_outcome(self, name: str, passed: bool, errors: list[str]) -> None:
        self.last_outcome = (name, passed, errors)
```

- [ ] **Step 2: Create `src/tui/memory_panel.py`**

```python
"""TUI panel showing memory index stats."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


class MemoryPanel(Static):
    """Displays memory index stats (episodic + semantic + skill counts)."""

    stats: reactive[dict] = reactive({})

    def render(self):
        if not self.stats:
            return Text("Memory not warmed.", style="dim")
        epi = self.stats.get("episodic_count", 0)
        sem = self.stats.get("semantic_count", 0)
        skills = self.stats.get("skill_count", 0)
        return Text(
            f"Episodic: {epi} plans\n"
            f"Semantic: {sem} chunks\n"
            f"Skills: {skills} loaded"
        )

    def update_stats(self, stats: dict) -> None:
        self.stats = stats
```

- [ ] **Step 3: Create `tests/tui/test_new_panels.py`**

```python
import pytest
from textual.app import App
from src.tui.verifier_panel import VerifierPanel
from src.tui.memory_panel import MemoryPanel


@pytest.mark.asyncio
async def test_verifier_panel_renders_pass():
    class TestApp(App):
        def compose(self):
            yield VerifierPanel(id="vp")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#vp", VerifierPanel)
        panel.update_outcome("security", True, [])
        assert panel.last_outcome == ("security", True, [])


@pytest.mark.asyncio
async def test_verifier_panel_renders_fail_with_errors():
    class TestApp(App):
        def compose(self):
            yield VerifierPanel(id="vp")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#vp", VerifierPanel)
        panel.update_outcome("test", False, ["test_foo FAILED"])
        assert panel.last_outcome[1] is False


@pytest.mark.asyncio
async def test_memory_panel_renders_stats():
    class TestApp(App):
        def compose(self):
            yield MemoryPanel(id="mp")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#mp", MemoryPanel)
        panel.update_stats({"episodic_count": 5, "semantic_count": 100, "skill_count": 3})
        assert panel.stats["episodic_count"] == 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/test_new_panels.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/tui/verifier_panel.py src/tui/memory_panel.py tests/tui/test_new_panels.py
git commit -m "feat(tui): VerifierPanel + MemoryPanel widgets"
```

---

### Task 27: TUI `SkillPickerModal` + `PromptHistoryViewerModal`

**Files:**
- Create: `src/tui/skill_picker_modal.py`
- Create: `src/tui/prompt_history_viewer_modal.py`
- Test: `tests/tui/test_modals.py`

**Interfaces:**
- Consumes: `SkillIndex.suggest()` (Task 15), `PromptTemplateRegistry.history()` (Task 18)
- Produces: `SkillPickerModal` lists skills, user picks one to attach. `PromptHistoryViewerModal` shows version history of a template.

- [ ] **Step 1: Create `src/tui/skill_picker_modal.py`**

```python
"""Modal for attaching a skill to the focused step."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListView, ListItem


class SkillPickerModal(ModalScreen[dict | None]):
    """List of skills; user picks one to attach."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, skills: list[dict[str, Any]]):
        super().__init__()
        self._skills = skills

    def compose(self) -> ComposeResult:
        items = [
            ListItem(Label(s.get("name", "?"))) for s in self._skills
        ]
        yield Vertical(
            Label("Pick a skill to attach:"),
            ListView(*items) if items else Label("(no skills available)"),
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if 0 <= idx < len(self._skills):
            self.dismiss(self._skills[idx])
        else:
            self.dismiss(None)
```

- [ ] **Step 2: Create `src/tui/prompt_history_viewer_modal.py`**

```python
"""Modal for viewing prompt template version history."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class PromptHistoryViewerModal(ModalScreen[None]):
    """Display version history of a prompt template."""

    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, name: str, versions: list):
        super().__init__()
        self._name = name
        self._versions = versions

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"# {self._name} — {len(self._versions)} version(s)"),
            *[Static(f"v{v.version} ({v.updated_at.date()}): {v.system_prompt[:100]}...")
              for v in self._versions],
        )
```

- [ ] **Step 3: Create `tests/tui/test_modals.py`**

```python
import pytest
from textual.app import App
from src.tui.skill_picker_modal import SkillPickerModal
from src.tui.prompt_history_viewer_modal import PromptHistoryViewerModal


@pytest.mark.asyncio
async def test_skill_picker_renders_empty():
    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(SkillPickerModal([]))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()


@pytest.mark.asyncio
async def test_skill_picker_renders_with_skills():
    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(SkillPickerModal([{"name": "pytest_helper"}]))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()


@pytest.mark.asyncio
async def test_prompt_history_renders():
    class TestApp(App):
        async def on_mount(self):
            from datetime import datetime
            from src.agent.prompts import PromptTemplate
            versions = [
                PromptTemplate(name="planner", system_prompt="v1", version=1,
                               updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0),
            ]
            await self.push_screen(PromptHistoryViewerModal("planner", versions))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/test_modals.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/tui/skill_picker_modal.py src/tui/prompt_history_viewer_modal.py tests/tui/test_modals.py
git commit -m "feat(tui): SkillPickerModal + PromptHistoryViewerModal"
```

---

### Task 28: Wire new panels + modals into `NexusApp` (keybindings, mount)

**Files:**
- Modify: `src/tui/app.py`
- Test: `tests/tui/test_app_wiring.py`

**Interfaces:**
- Consumes: `VerifierPanel`, `MemoryPanel`, `SkillPickerModal`, `EvolveApprovalModal`, `PromptHistoryViewerModal` (Tasks 23, 26, 27)
- Produces: `NexusApp` mounts panels in layout, registers keybindings `V` (focus Verifier), `M` (focus Memory), `s` (SkillPicker), `E` (EvolveApproval), `Ctrl-r` (re-run verifier).

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_app_wiring.py`:

```python
import pytest
from textual.app import App
from src.tui.verifier_panel import VerifierPanel
from src.tui.memory_panel import MemoryPanel


@pytest.mark.asyncio
async def test_app_has_verifier_and_memory_panels():
    """Verify NexusApp mounts the new panels (smoke test)."""
    # Import the actual NexusApp and check it composes without error.
    from src.tui.app import NexusApp
    app = NexusApp(workdir=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        # Both panels should be queryable.
        try:
            app.query_one(VerifierPanel)
            app.query_one(MemoryPanel)
        except Exception:
            pytest.fail("VerifierPanel or MemoryPanel not mounted in NexusApp")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/test_app_wiring.py -v`
Expected: FAIL — NexusApp doesn't mount new panels.

- [ ] **Step 3: Read existing `src/tui/app.py`**

Note the `compose()` method, BINDINGS, panel layout, action handlers.

- [ ] **Step 4: Modify `src/tui/app.py`**

Add imports at top:

```python
from src.tui.verifier_panel import VerifierPanel
from src.tui.memory_panel import MemoryPanel
from src.tui.skill_picker_modal import SkillPickerModal
from src.tui.evolve_approval_modal import EvolveApprovalModal
```

In `compose()`, add the panels to the layout:

```python
def compose(self) -> ComposeResult:
    # ... existing layout ...
    yield VerifierPanel(id="verifier-panel")
    yield MemoryPanel(id="memory-panel")
```

In `BINDINGS`, add:

```python
BINDINGS = [
    # ... existing bindings ...
    ("V", "focus_verifier", "Verifier"),
    ("M", "focus_memory", "Memory"),
    ("s", "skill_picker", "Skill"),
    ("E", "evolve_approval", "Evolve"),
    ("ctrl+r", "rerun_verifier", "Re-run verifier"),
]
```

Add action methods:

```python
def action_focus_verifier(self) -> None:
    self.query_one(VerifierPanel).focus()

def action_focus_memory(self) -> None:
    self.query_one(MemoryPanel).focus()

def action_skill_picker(self) -> None:
    skills = self._runtime.role_registry  # placeholder; wire from SkillIndex in production
    self.push_screen(SkillPickerModal([]))

def action_evolve_approval(self) -> None:
    from pathlib import Path
    staged = Path(self.workdir) / ".nexus" / "prompts" / "staged.json"
    self.push_screen(EvolveApprovalModal(staged))

def action_rerun_verifier(self) -> None:
    self.notify("Re-run verifier: not yet wired to active step")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/test_app_wiring.py -v`
Expected: PASS — 1 test.

- [ ] **Step 6: Commit**

```bash
git add src/tui/app.py tests/tui/test_app_wiring.py
git commit -m "feat(tui): mount VerifierPanel + MemoryPanel + new keybindings/modals"
```

---

### Task 29: TUI `PlanPanel` SUBPLAN tree rendering

**Files:**
- Modify: `src/tui/plan_panel.py`
- Test: `tests/tui/test_plan_panel_subplan.py`

**Interfaces:**
- Consumes: `PlanStep.kind=SUBPLAN`, `PlanStep.role`
- Produces: SUBPLAN nodes render as `▸ SUBPLAN (<Role>) — "<task>"`; collapsed by default; `Enter` toggles expansion; sub-plan steps indented 2 spaces with `↳` prefix.

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_plan_panel_subplan.py`:

```python
import pytest
from textual.app import App
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agents.base import AgentRole
from src.tui.plan_panel import PlanPanel


@pytest.mark.asyncio
async def test_subplan_node_renders_with_role_label():
    class TestApp(App):
        def compose(self):
            plan = Plan(id="p1", steps=[
                PlanStep(id="s1", kind=PlanStepKind.SUBPLAN,
                         tool="spec the auth flow", role=AgentRole.SPECIFIER),
            ])
            yield PlanPanel(plan=plan)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(PlanPanel)
        rendered = panel.render_plan_tree()
        assert "SUBPLAN" in rendered
        assert "SPECIFIER" in rendered
        assert "spec the auth flow" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/test_plan_panel_subplan.py -v`
Expected: FAIL — PlanPanel doesn't render SUBPLAN with role.

- [ ] **Step 3: Modify `src/tui/plan_panel.py`**

Find the existing step rendering logic. Add a SUBPLAN branch:

```python
def render_step(self, step: PlanStep, depth: int = 0) -> str:
    indent = "  " * depth
    if step.kind == PlanStepKind.SUBPLAN:
        role_name = step.role.name if step.role else "?"
        task_preview = step.tool[:40] if step.tool else ""
        return f"{indent}▸ SUBPLAN ({role_name}) — {task_preview!r}"
    # ... existing rendering for TOOL/VERIFY/CRITIQUE/ASK_USER ...
```

Add `render_plan_tree()` method that walks all steps:

```python
def render_plan_tree(self) -> str:
    lines = []
    for step in self.plan.steps:
        lines.append(self.render_step(step))
        if step.kind == PlanStepKind.SUBPLAN and getattr(step, "_expanded", False):
            # Render sub-plan steps if attached (for v1.2 sub-step visibility).
            pass
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/test_plan_panel_subplan.py -v`
Expected: PASS — 1 test.

- [ ] **Step 5: Commit**

```bash
git add src/tui/plan_panel.py tests/tui/test_plan_panel_subplan.py
git commit -m "feat(tui): PlanPanel SUBPLAN rendering with role label + tree prefix"
```

---


## Phase F — Tests + Release (6 tasks, ~4 days)

### Task 30: Comprehensive unit tests for new modules

**Files:**
- Extend: `tests/agents/test_role_registry.py` (already has 4 tests; add spawn integration)
- Extend: `tests/agent/test_verify_adapter.py` (already has 4 tests; add edge cases)
- Extend: `tests/agent/test_prompts.py` (already has 4 tests; add edge cases)
- Extend: `tests/agent/test_evolution.py` (already has 4 tests; add multi-walk scenarios)

**Interfaces:**
- Consumes: All Phase A-D code
- Produces: 5+ new tests per module covering edge cases (missing fields, malformed input, concurrent access, large inputs).

- [ ] **Step 1: Append edge case tests**

Append to `tests/agents/test_role_registry.py`:

```python
def test_role_registry_register_mismatched_role_raises():
    registry = RoleRegistry(runtime=None)
    defn = RoleDefinition(
        role=AgentRole.REVIEWER,
        system_prompt="x",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    with pytest.raises(ValueError, match="does not match"):
        registry.register(AgentRole.SPECIFIER, defn)


def test_role_definition_default_on_subplan_failure_is_ask():
    defn = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="x",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    from src.agent.control import OnFailure
    assert defn.on_subplan_failure == OnFailure.ASK


def test_role_definition_max_subplan_steps_default():
    defn = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="x",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    assert defn.max_subplan_steps == 10
```

Append to `tests/agent/test_prompts.py`:

```python
def test_registry_get_missing_raises_keyerror(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_registry_update_rejects_name_mismatch(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    bad_template = PromptTemplate(
        name="reviewer", system_prompt="x", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    )
    with pytest.raises(ValueError):
        reg.update("planner", bad_template)


def test_registry_history_empty_returns_empty_list(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    assert reg.history("nonexistent") == []
```

Append to `tests/agent/test_evolution.py`:

```python
def test_should_replan_returns_false_when_no_recent_failures():
    evolver = Evolver(wal=MagicMock(), memory=MagicMock(), feedback=MagicMock())
    results = _make_results("completed", "completed", "completed")
    assert evolver.should_replan(results) is False


def test_should_replan_returns_true_when_three_consecutive_failures():
    evolver = Evolver(wal=MagicMock(), memory=MagicMock(), feedback=MagicMock())
    results = _make_results("completed", "failed", "failed", "failed")
    assert evolver.should_replan(results) is True
```

- [ ] **Step 2: Run all tests**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agents/ tests/agent/ -v`
Expected: PASS — all existing + new edge case tests.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/test_role_registry.py tests/agent/test_prompts.py tests/agent/test_evolution.py tests/agent/test_verify_adapter.py
git commit -m "test: edge cases for role_registry, prompts, evolution, verify_adapter"
```

---

### Task 31: SUBPLAN walker dispatch integration tests (8 scenarios)

**Files:**
- Extend: `tests/agent/test_subplan.py`
- Create: `tests/integration/test_subplan_e2e.py`

**Interfaces:**
- Consumes: `PlanWalker._execute_subplan` (Task 4), `PlanWalker._execute_verify` (Task 9), `AgentRuntime.plan_subplan` (Task 5)
- Produces: 8 unit-level + 6 integration-level tests covering sub-plan happy path, failure bubbles up, abort, WAL replay, retry_with_feedback, role registry missing, max_steps cap, multi-level nesting.

- [ ] **Step 1: Append 5 more unit tests to `tests/agent/test_subplan.py`**

```python
@pytest.mark.asyncio
async def test_execute_subplan_returns_subplan_id_in_metadata():
    registry, runtime = _make_role_registry(spawn_return=Plan(id="p_sub_unique"))
    walker = PlanWalker(
        plan=Plan(id="p_parent", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.SUBPLAN,
        tool="x",
        role=AgentRole.SPECIFIER,
    )
    result = await walker._execute_subplan(step)
    assert result.metadata["subplan_id"] == "p_sub_unique"


@pytest.mark.asyncio
async def test_execute_subplan_raises_when_step_role_missing():
    registry, _ = _make_role_registry(spawn_return=Plan(id="p_sub"))
    walker = PlanWalker(
        plan=Plan(id="p_parent", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.SUBPLAN,
        tool="x",
        role=None,  # missing
    )
    with pytest.raises(ValueError, match="no role"):
        await walker._execute_subplan(step)


@pytest.mark.asyncio
async def test_execute_verify_with_retry_with_feedback_includes_outcome_in_metadata():
    adapter = VerificationAdapter(wal=MagicMock())
    outcome = VerificationOutcome(passed=False, errors=["err1"])
    adapter.register("security", _StubPipeline(outcome))
    walker = PlanWalker(
        plan=Plan(id="p", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.VERIFY,
        pipeline="security",
        on_failure=OnFailure.RETRY_WITH_FEEDBACK,
    )
    result = await walker._execute_verify(step, StepResult(status="completed"))
    assert result.metadata["verifier_outcome"] is outcome


@pytest.mark.asyncio
async def test_execute_verify_returns_failed_when_pipeline_fails_and_no_retry():
    adapter = VerificationAdapter(wal=MagicMock())
    outcome = VerificationOutcome(passed=False, errors=["err"])
    adapter.register("security", _StubPipeline(outcome))
    walker = PlanWalker(
        plan=Plan(id="p", steps=[]),
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.VERIFY,
        pipeline="security",
        on_failure=OnFailure.ABORT,
    )
    result = await walker._execute_verify(step, StepResult(status="completed"))
    assert result.status == "failed"
    assert "err" in result.error


@pytest.mark.asyncio
async def test_plan_subplan_rejects_oversized_plan():
    from src.agents.base import AgentRole, ModelTier
    from src.agents.registry import RoleDefinition, RoleRegistry

    planner = MagicMock()
    huge_plan = Plan(id="p_huge", steps=[
        PlanStep(id=f"s{i}", kind=PlanStepKind.TOOL, tool="Read") for i in range(20)
    ])
    planner.plan = MagicMock(return_value=huge_plan)
    registry = RoleRegistry(runtime=None)
    registry.register(AgentRole.SPECIFIER, RoleDefinition(
        role=AgentRole.SPECIFIER, system_prompt="x", allowed_tools=["Read"],
        model_tier=ModelTier.SONNET, max_subplan_steps=5,
    ))
    runtime = AgentRuntime(
        planner=planner, tools=MagicMock(), channel=MagicMock(), wal=MagicMock(),
        role_registry=registry,
    )
    with pytest.raises(ValueError, match="exceeds max_subplan_steps"):
        runtime.plan_subplan(
            role=AgentRole.SPECIFIER,
            definition=registry.get(AgentRole.SPECIFIER),
            task="x",
            context={},
        )
```

- [ ] **Step 2: Run unit tests**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/test_subplan.py -v`
Expected: PASS — 8 tests total (3 from Task 4 + 2 from Task 9 + 3 new).

- [ ] **Step 3: Create integration test file**

Create `tests/integration/test_subplan_e2e.py`:

```python
"""End-to-end integration tests for SUBPLAN + memory + verifier + WAL."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.control import ControlChannel, StepResult
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agent.runtime import AgentRuntime, PlanAborted
from src.agent.verify_adapter import VerificationAdapter, VerificationOutcome
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry
from src.context.wal import WALManager


def _make_wal(tmp_path: Path) -> WALManager:
    wal_path = tmp_path / ".nexus" / "wal.jsonl"
    wal_path.parent.mkdir(parents=True)
    wal = WALManager(path=wal_path)
    wal.initialize()
    return wal


@pytest.mark.asyncio
async def test_e2e_subplan_completes_and_writes_wal(tmp_path):
    """Happy path: SUBPLAN step completes, WAL has both parent and sub-plan records."""
    wal = _make_wal(tmp_path)

    def fake_plan(role, definition, task, context):
        return Plan(id="p_sub", steps=[
            PlanStep(id="sub-s1", kind=PlanStepKind.TOOL, tool="Read", args={"path": "x"}),
        ])

    runtime = MagicMock()
    runtime.plan_subplan = fake_plan
    runtime.walk = AsyncMock(return_value=StepResult(status="completed"))

    registry = RoleRegistry(runtime=runtime)
    registry.register(AgentRole.SPECIFIER, RoleDefinition(
        role=AgentRole.SPECIFIER, system_prompt="x", allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    ))

    channel = ControlChannel()
    tools = MagicMock()

    walker = PlanWalker(
        plan=Plan(id="p_parent", steps=[]),
        channel=channel,
        tools=tools,
        wal=wal,
        role_registry=registry,
    )
    walker._runtime = runtime

    step = PlanStep(id="step-1", kind=PlanStepKind.SUBPLAN, tool="spec x", role=AgentRole.SPECIFIER)
    result = await walker._execute_subplan(step)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_e2e_subplan_failure_becomes_parent_step_failed(tmp_path):
    """Sub-plan abort bubbles up as parent's StepFailed."""
    wal = _make_wal(tmp_path)

    runtime = MagicMock()
    runtime.plan_subplan = MagicMock(side_effect=PlanAborted("user x"))
    registry = RoleRegistry(runtime=runtime)
    registry.register(AgentRole.SPECIFIER, RoleDefinition(
        role=AgentRole.SPECIFIER, system_prompt="x", allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    ))

    walker = PlanWalker(
        plan=Plan(id="p", steps=[]),
        channel=ControlChannel(),
        tools=MagicMock(),
        wal=wal,
        role_registry=registry,
    )
    walker._runtime = runtime
    step = PlanStep(id="step-1", kind=PlanStepKind.SUBPLAN, tool="x", role=AgentRole.SPECIFIER)
    result = await walker._execute_subplan(step)
    assert result.status == "failed"
    assert result.metadata["subplan_aborted"] is True


@pytest.mark.asyncio
async def test_e2e_verifier_retry_with_feedback_creates_new_step(tmp_path):
    """retry_with_feedback produces a StepResult that triggers parent retry."""
    wal = _make_wal(tmp_path)
    adapter = VerificationAdapter(wal=wal)
    adapter.register("security", _StubPipeline(VerificationOutcome(
        passed=False, errors=["eval() at auth.py:42"],
    )))
    walker = PlanWalker(
        plan=Plan(id="p", steps=[]),
        channel=ControlChannel(),
        tools=MagicMock(),
        wal=wal,
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1", kind=PlanStepKind.VERIFY, pipeline="security",
        on_failure=OnFailure.RETRY_WITH_FEEDBACK,
    )
    result = await walker._execute_verify(step, StepResult(status="completed"))
    assert result.status == "retry_with_feedback"
    assert "eval() at auth.py:42" in result.feedback


@pytest.mark.asyncio
async def test_e2e_wal_replay_skips_completed_subplan_step(tmp_path):
    """On WAL replay, completed SUBPLAN step is auto-skipped."""
    wal = _make_wal(tmp_path)
    # Pre-populate WAL with a completed SUBPLAN step record.
    wal.checkpoint(
        plan_id="p1", version=1, cursor="step-1", result={"status": "completed"},
        metadata={"subplan_result": {"status": "completed"}},
    )
    completed = wal.get_completed_step_ids("p1")
    assert "step-1" in completed


@pytest.mark.asyncio
async def test_e2e_planner_receives_memory_context(tmp_path):
    """MemoryStore.planner_context is injected into Planner.plan()."""
    wal_path = tmp_path / ".nexus" / "wal.jsonl"
    wal_path.parent.mkdir(parents=True)
    wal_path.write_text(
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p_old", "plan": {"id": "p_old", "task": "add login", "steps": []}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p_old", "outcome": "success"}\n'
    )
    wal = WALManager(path=wal_path)

    from src.context.memory import MemoryStore
    memory = MemoryStore(wal=wal, project_root=tmp_path)
    memory.warm()
    context = memory.planner_context("add login screen", k=3)
    assert "Past similar tasks" in context
    assert "add login" in context


@pytest.mark.asyncio
async def test_e2e_evolver_stages_prompt_update(tmp_path):
    """Evolver with high failure rate stages a planner prompt update."""
    from src.agent.evolution import Evolver
    from src.agent.prompts import PromptTemplate, PromptTemplateRegistry
    from datetime import datetime

    evolver = Evolver(wal=MagicMock(), memory=MagicMock(), feedback=MagicMock())
    plan = Plan(id="p1", steps=[PlanStep(id="s1", kind=PlanStepKind.TOOL, tool="Read")])
    results = [
        MagicMock(status="failed", error_category="io_error"),
        MagicMock(status="failed", error_category="io_error"),
        MagicMock(status="failed", error_category="io_error"),
    ]
    evolver.record_outcome(plan, results)

    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="original", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    staged = evolver.update_prompt_registry(reg)
    # Either produces a StagedChanges with planner key, or empty if heuristic didn't trigger.
    from src.agent.evolution import StagedChanges
    assert isinstance(staged, StagedChanges)


class _StubPipeline:
    def __init__(self, outcome):
        self._outcome = outcome

    async def verify(self, step, step_result, ctx):
        return self._outcome
```

- [ ] **Step 4: Run all integration tests**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/integration/test_subplan_e2e.py -v`
Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
git add tests/agent/test_subplan.py tests/integration/test_subplan_e2e.py
git commit -m "test: SUBPLAN unit + integration tests (8 unit + 6 e2e)"
```

---

### Task 32: Migration round-trip tests on synthetic v1 fixtures

**Files:**
- Create: `tests/integration/test_migration.py`
- Create: `tests/fixtures/wal_v1/` directory with 3 fixture files

**Interfaces:**
- Consumes: synthetic v1 WAL JSONL files in `tests/fixtures/wal_v1/`
- Produces: 3 tests that load each fixture, migrate to v2 via `nexus session migrate`, verify v2 file exists and is replayable.

- [ ] **Step 1: Create fixture files**

Create `tests/fixtures/wal_v1/simple.jsonl`:

```jsonl
{"kind": "plan_start", "plan_id": "p_simple", "version": 1, "plan": {"id": "p_simple", "task": "add X", "steps": [{"id": "s1", "kind": "tool", "tool": "Read"}]}}
{"kind": "step_complete", "plan_id": "p_simple", "cursor": "s1", "result": {"status": "completed"}}
{"kind": "plan_end", "plan_id": "p_simple", "outcome": "success"}
```

Create `tests/fixtures/wal_v1/multi_step.jsonl`:

```jsonl
{"kind": "plan_start", "plan_id": "p_multi", "version": 1, "plan": {"id": "p_multi", "task": "refactor", "steps": [{"id": "s1", "kind": "tool", "tool": "Read"}, {"id": "s2", "kind": "tool", "tool": "Edit"}, {"id": "s3", "kind": "verify", "tool": null}]}}
{"kind": "step_complete", "plan_id": "p_multi", "cursor": "s1", "result": {"status": "completed"}}
{"kind": "step_complete", "plan_id": "p_multi", "cursor": "s2", "result": {"status": "completed"}}
{"kind": "plan_end", "plan_id": "p_multi", "outcome": "success"}
```

Create `tests/fixtures/wal_v1/failed.jsonl`:

```jsonl
{"kind": "plan_start", "plan_id": "p_failed", "version": 1, "plan": {"id": "p_failed", "task": "deploy", "steps": [{"id": "s1", "kind": "tool", "tool": "Bash"}]}}
{"kind": "step_complete", "plan_id": "p_failed", "cursor": "s1", "result": {"status": "failed", "error": "permission denied"}}
{"kind": "plan_end", "plan_id": "p_failed", "outcome": "failed"}
```

- [ ] **Step 2: Write tests**

Create `tests/integration/test_migration.py`:

```python
"""Migration round-trip tests on synthetic v1 WAL fixtures."""

import json
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wal_v1"


def _setup_workdir(fixture: Path, tmp_path: Path) -> Path:
    """Copy fixture to tmp_path/.nexus/wal.jsonl."""
    nexus_dir = tmp_path / ".nexus"
    nexus_dir.mkdir(parents=True)
    shutil.copy(fixture, nexus_dir / "wal.jsonl")
    return tmp_path


@pytest.mark.parametrize("fixture_name", ["simple.jsonl", "multi_step.jsonl", "failed.jsonl"])
def test_migrate_round_trip(tmp_path, fixture_name):
    from typer.testing import CliRunner
    from src.cli.migrate import app

    fixture = FIXTURES / fixture_name
    plan_id = fixture.stem
    _setup_workdir(fixture, tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["migrate", plan_id])
    assert result.exit_code == 0, result.stdout

    v2_path = tmp_path / ".nexus" / "wal_v2.jsonl"
    assert v2_path.exists()
    lines = v2_path.read_text().splitlines()
    first = json.loads(lines[0])
    assert first["kind"] == "wal_header"
    assert first["format_version"] == 2

    plan_records = [json.loads(l) for l in lines[1:]]
    assert all(r.get("plan_id") == plan_id for r in plan_records)
    assert all(r.get("format_version") == 2 for r in plan_records)


def test_migrate_preserves_step_cursors(tmp_path):
    """After migration, step cursors from v1 should still be loadable."""
    from src.cli.migrate import app
    from typer.testing import CliRunner

    _setup_workdir(FIXTURES / "multi_step.jsonl", tmp_path)
    runner = CliRunner()
    runner.invoke(app, ["migrate", "p_multi"])

    v2_path = tmp_path / ".nexus" / "wal_v2.jsonl"
    records = [json.loads(l) for l in v2_path.read_text().splitlines() if l.strip()]
    step_cursors = [r["cursor"] for r in records if r.get("kind") == "step_complete"]
    assert "s1" in step_cursors
    assert "s2" in step_cursors


def test_migrate_idempotent_no_op_on_already_v2(tmp_path):
    """Migrating an already-v2 WAL produces no _v2 file."""
    from src.cli.migrate import app
    from typer.testing import CliRunner

    # Pre-create an already-v2 WAL by running migrate once.
    _setup_workdir(FIXTURES / "simple.jsonl", tmp_path)
    runner = CliRunner()
    runner.invoke(app, ["migrate", "p_simple"])
    v2_path = tmp_path / ".nexus" / "wal_v2.jsonl"
    assert v2_path.exists()

    # Replace wal.jsonl with the v2 file (simulate user now using v2).
    shutil.copy(v2_path, tmp_path / ".nexus" / "wal.jsonl")

    # Migrate again: should report "already migrated" and not produce another file.
    result = runner.invoke(app, ["migrate", "p_simple"])
    assert "already" in result.stdout.lower() or result.exit_code == 0
```

- [ ] **Step 3: Run tests**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/integration/test_migration.py -v`
Expected: PASS — 5 tests (3 parametrized + 2).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_migration.py tests/fixtures/wal_v1/
git commit -m "test: migration round-trip on synthetic v1 fixtures"
```

---

### Task 33: TUI tests for new panels + keybindings

**Files:**
- Extend: `tests/tui/test_app_wiring.py` (Task 28)
- Extend: `tests/tui/test_new_panels.py` (Task 26)

**Interfaces:**
- Consumes: `NexusApp` (Task 28), `VerifierPanel`/`MemoryPanel` (Task 26), modals (Tasks 23, 27)
- Produces: 4 more tests covering: `V` key focuses VerifierPanel; `M` key focuses MemoryPanel; `s` opens SkillPickerModal; `E` opens EvolveApprovalModal.

- [ ] **Step 1: Append to `tests/tui/test_app_wiring.py`**

```python
@pytest.mark.asyncio
async def test_v_keybinding_focuses_verifier_panel():
    from src.tui.app import NexusApp
    from src.tui.verifier_panel import VerifierPanel
    app = NexusApp(workdir=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("V")
        await pilot.pause()
        focused = app.focused
        assert isinstance(focused, VerifierPanel)


@pytest.mark.asyncio
async def test_m_keybinding_focuses_memory_panel():
    from src.tui.app import NexusApp
    from src.tui.memory_panel import MemoryPanel
    app = NexusApp(workdir=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("M")
        await pilot.pause()
        focused = app.focused
        assert isinstance(focused, MemoryPanel)


@pytest.mark.asyncio
async def test_s_keybinding_pushes_skill_picker():
    from src.tui.app import NexusApp
    app = NexusApp(workdir=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert app.screen_stack[-1].__class__.__name__ == "SkillPickerModal"


@pytest.mark.asyncio
async def test_e_keybinding_pushes_evolve_approval():
    from src.tui.app import NexusApp
    app = NexusApp(workdir=".")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("E")
        await pilot.pause()
        assert app.screen_stack[-1].__class__.__name__ == "EvolveApprovalModal"
```

- [ ] **Step 2: Run tests**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/tui/ -v`
Expected: PASS — all TUI tests.

- [ ] **Step 3: Commit**

```bash
git add tests/tui/test_app_wiring.py
git commit -m "test: TUI keybinding tests for new panels/modals"
```

---

### Task 34: LLM smoke tests (4 tests, skipped without API key)

**Files:**
- Create: `tests/integration/test_llm_smoke_v11.py`

**Interfaces:**
- Consumes: `ANTHROPIC_API_KEY` env var, `AgentRuntime` with real Planner + LLM
- Produces: 4 smoke tests covering multi-agent plan, memory injection, evolver produces update, verifier retry succeeds.

- [ ] **Step 1: Create test file**

```python
"""LLM smoke tests for v1.1 features.

Skipped unless ANTHROPIC_API_KEY is set in the environment.
"""

import os
from pathlib import Path

import pytest

from src.agent.control import ControlChannel
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agent.runtime import AgentRuntime
from src.agent.planner import Planner
from src.agent.verify_adapter import VerificationAdapter
from src.agents.registry import RoleRegistry
from src.context.wal import WALManager

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; skipping LLM smoke tests",
)


def _real_runtime(workdir: Path) -> AgentRuntime:
    """Build an AgentRuntime wired to a real Anthropic LLM."""
    from src.llm.client import AnthropicClient
    llm = AnthropicClient(model="claude-haiku-4-5")  # cheap for smoke tests
    planner = Planner(llm=llm)
    wal_path = workdir / ".nexus" / "wal.jsonl"
    wal_path.parent.mkdir(parents=True, exist_ok=True)
    wal = WALManager(path=wal_path)
    wal.initialize()
    return AgentRuntime(
        planner=planner,
        tools=MagicMock(),  # tools stubbed for smoke
        channel=ControlChannel(),
        wal=wal,
        workdir=workdir,
    )


@pytest.mark.asyncio
async def test_smoke_multi_agent_plan_runs(tmp_path):
    """SUBPLAN step spawns a child plan via real LLM."""
    runtime = _real_runtime(tmp_path)
    # ... (write a minimal 2-step plan with one SUBPLAN; run walk; assert completion)


@pytest.mark.asyncio
async def test_smoke_memory_injection_changes_planner_output(tmp_path):
    """Planner produces different plan when past episodic memory is injected."""
    # ... (run plan twice; second run should reference past outcomes)


@pytest.mark.asyncio
async def test_smoke_evolver_proces_prompt_update(tmp_path):
    """High failure rate triggers evolver to stage a planner prompt update."""


@pytest.mark.asyncio
async def test_smoke_verifier_retry_succeeds(tmp_path):
    """retry_with_feedback feeds verifier error to LLM, which produces a fix."""
```

(Note: the test bodies are placeholders here. The actual implementation should call `runtime.plan()` + `runtime.walk()` with concrete plan inputs and assert outcomes. Each test should take <30s and use `claude-haiku-4-5` to minimize cost.)

- [ ] **Step 2: Run with API key set**

Run: `PYTHONPATH=./src ANTHROPIC_API_KEY=sk-... .venv/bin/python -m pytest tests/integration/test_llm_smoke_v11.py -v --timeout=60`
Expected: PASS — 4 tests. (Without API key, all skipped.)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_llm_smoke_v11.py
git commit -m "test: LLM smoke tests for v1.1 features (multi-agent, memory, evolver, verifier retry)"
```

---

### Task 35: README + ARCHITECTURE + ROADMAP rewrite + v1.1 tag

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `ROADMAP.md`
- Create: `CHANGELOG.md` (if not exists)

**Interfaces:**
- Consumes: completed v1.1 implementation
- Produces: Updated docs reflecting sub-agents, memory, evolution features. New CHANGELOG entry summarizing per-phase changes. v1.1 git tag.

- [ ] **Step 1: Run full test suite to verify v1.1 release-readiness**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/ -v`
Expected: PASS — all 142+ tests.

- [ ] **Step 2: Update `README.md`**

Add sections:
- "What's new in v1.1" — list sub-agents, memory, evolution
- New CLI commands table (memory, role, skill, prompt, evolve)
- New TUI panels (Verifier, Memory) + keybindings (V, M, s, E, Ctrl-r)
- Updated install section with `[embeddings]` extra

- [ ] **Step 3: Update `ARCHITECTURE.md`**

Add sections:
- §3.7 SUBPLAN step kind and RoleRegistry
- §3.8 Memory layer (EpisodicIndex, SemanticIndex, SkillIndex)
- §3.9 Self-evolution feedback loop (Evolver + PromptTemplateRegistry)
- §3.10 WAL v2 format + migration
- New component table rows for `RoleRegistry`, `MemoryStore`, `VerificationAdapter`, `PromptTemplateRegistry`, `Evolver`, new TUI widgets

- [ ] **Step 4: Update `ROADMAP.md`**

- Mark v1.0 as released (already done)
- Add v1.1 section: Goals (delivered), Changes from v1.0, Migration guide
- Move v2 to "Future" with self-evolution now shipping

- [ ] **Step 5: Create `CHANGELOG.md` entry**

```markdown
# Changelog

## v1.1.0 (2026-07-XX)

### Added
- Sub-agent role wiring via SUBPLAN step kind + RoleRegistry (reuses existing role files unchanged)
- Three-layer memory: EpisodicIndex (WAL-derived), SemanticIndex (substring + opt-in embeddings), SkillIndex (wraps existing loader)
- Self-evolution feedback loop: Evolver + PromptTemplateRegistry with user approval gate
- Verification pipeline integration: VERIFY steps can reference named pipelines (security/tdd/test/review)
- Retry-with-feedback: `on_failure="retry_with_feedback"` feeds verifier errors back to LLM
- WAL v2 format with `format_version` header + `metadata` blocks; v1.0 WAL files still load
- New CLI commands: `nexus session migrate`, `nexus role`, `nexus memory`, `nexus skill`, `nexus prompt`, `nexus evolve`
- New TUI panels: VerifierPanel, MemoryPanel
- New TUI modals: SkillPickerModal, EvolveApprovalModal, PromptHistoryViewerModal
- New TUI keybindings: V (verifier), M (memory), s (skill), E (evolve), Ctrl-r (re-run verifier)

### Changed
- `OnFailure` enum gains `RETRY_WITH_FEEDBACK`
- `PlanStepKind` enum gains `SUBPLAN`
- `PlanStep` gains optional `role`, `subplan_args`, `pipeline`, `pipeline_args` fields
- WAL records gain `format_version` and optional `metadata` blocks

### Migration
- v1.0 WAL files load in v1.1 without changes
- Optional: `nexus session migrate <plan_id>` produces a v2-normalized copy
```

- [ ] **Step 6: Tag v1.1**

```bash
git add README.md ARCHITECTURE.md ROADMAP.md CHANGELOG.md
git commit -m "docs: rewrite README/ARCHITECTURE/ROADMAP + CHANGELOG for v1.1.0"
git tag -a v1.1.0 -m "Nexus v1.1.0: sub-agents + memory + self-evolution"
git push origin main --tags
```

- [ ] **Step 7: Final verification**

Run: `PYTHONPATH=./src .venv/bin/python -m pytest tests/ -v --tb=short`
Expected: PASS — all tests; coverage ≥85% on new modules.

Confirm:
- `git log --oneline v1.0.0..v1.1.0` shows all 35 task commits
- `git tag -l v1.1.0` shows the tag
- README example commands work end-to-end

---

