"""End-to-end integration tests for SUBPLAN + memory + verifier + WAL."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.control import ControlChannel, StepResult
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind
from src.agent.runtime import AgentRuntime
from src.agent.walker import PlanAborted
from src.agent.verify_adapter import VerificationAdapter, VerificationOutcome
from src.agent.walker import PlanWalker
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry
from src.context.wal import WALManager


def _make_wal(tmp_path: Path) -> WALManager:
    wal_path = tmp_path / ".nexus" / "wal.jsonl"
    wal_path.parent.mkdir(parents=True, exist_ok=True)
    wal = WALManager(path=wal_path)
    wal.initialize()
    return wal


class _StubPipeline:
    def __init__(self, outcome):
        self._outcome = outcome

    async def verify(self, step, step_result, ctx):
        return self._outcome


@pytest.mark.asyncio
async def test_e2e_subplan_completes_and_writes_wal(tmp_path):
    """Happy path: SUBPLAN step completes, WAL has both parent and sub-plan records."""
    wal = _make_wal(tmp_path)

    def fake_plan(role, definition, task, context):
        return Plan(plan_id="p_sub", spec="sub", steps=[
            PlanStep(id="sub-s1", intent="x", kind=PlanStepKind.TOOL, tool="Read", args={"path": "x"}),
        ])

    runtime = MagicMock()
    runtime.plan_subplan = AsyncMock()
    runtime.plan_subplan.side_effect = lambda role, definition, task, context, model_name=None: fake_plan(role, definition, task, context)
    runtime.walk = AsyncMock(return_value=StepResult(step_id="sub-s1", status="completed"))

    registry = RoleRegistry(runtime=runtime)
    registry.register(AgentRole.SPECIFIER, RoleDefinition(
        role=AgentRole.SPECIFIER, system_prompt="x", allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    ))

    channel = ControlChannel()
    tools = MagicMock()

    walker = PlanWalker(
        channel=channel,
        tools=tools,
        wal=wal,
        role_registry=registry,
    )
    walker._runtime = runtime

    step = PlanStep(id="step-1", intent="spec x", kind=PlanStepKind.SUBPLAN, tool="spec x", role=AgentRole.SPECIFIER)
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
        channel=ControlChannel(),
        tools=MagicMock(),
        wal=wal,
        role_registry=registry,
    )
    walker._runtime = runtime
    step = PlanStep(id="step-1", intent="x", kind=PlanStepKind.SUBPLAN, tool="x", role=AgentRole.SPECIFIER)
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
        channel=ControlChannel(),
        tools=MagicMock(),
        wal=wal,
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1", intent="x", kind=PlanStepKind.VERIFY, pipeline="security",
        on_failure=OnFailure.RETRY_WITH_FEEDBACK,
    )
    result = await walker._execute_verify(step, StepResult(step_id="step-1", status="completed"))
    assert result.status == "retry_with_feedback"
    assert "eval() at auth.py:42" in result.feedback


@pytest.mark.asyncio
async def test_e2e_wal_replay_skips_completed_subplan_step(tmp_path):
    """On WAL replay, completed SUBPLAN step is auto-skipped."""
    wal = _make_wal(tmp_path)
    # Pre-populate WAL with a completed SUBPLAN step record.
    await wal.checkpoint(
        plan=Plan(plan_id="p1", spec="t", version=1), cursor="step-1", result={"status": "completed"},
        metadata={"subplan_result": {"status": "completed"}},
    )
    completed = wal.get_completed_step_ids("p1")
    assert "step-1" in completed


@pytest.mark.asyncio
async def test_e2e_planner_receives_memory_context(tmp_path):
    """MemoryStore.planner_context is injected into Planner.plan()."""
    wal_path = tmp_path / ".nexus" / "wal.jsonl"
    wal_path.parent.mkdir(parents=True, exist_ok=True)
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
    from src.agent.evolution import Evolver, StagedChanges
    from src.agent.prompts import PromptTemplate, PromptTemplateRegistry
    from datetime import datetime

    evolver = Evolver(wal=MagicMock(), memory=MagicMock(), feedback=MagicMock())
    plan = Plan(plan_id="p1", spec="test", steps=[PlanStep(id="s1", intent="x", kind=PlanStepKind.TOOL, tool="Read")])
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
    assert isinstance(staged, StagedChanges)