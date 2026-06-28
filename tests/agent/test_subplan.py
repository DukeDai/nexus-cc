import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.control import StepResult
from src.agent.plan import Plan, PlanStep, PlanStepKind, OnFailure
from src.agent.walker import PlanWalker, PlanAborted
from src.agent.verify_adapter import VerificationAdapter, VerificationOutcome
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry


def _make_role_registry(spawn_return: Plan, *, raises: Exception | None = None) -> RoleRegistry:
    runtime = MagicMock()
    runtime.plan_subplan = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=spawn_return)
    runtime.walk = AsyncMock(return_value=StepResult(step_id="sub-step", status="completed"))
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
    registry, runtime = _make_role_registry(spawn_return=Plan(plan_id="p_sub", spec="sub"))
    walker = PlanWalker(
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    step = PlanStep(
        id="step-1",
        intent="spec auth flow",
        kind=PlanStepKind.SUBPLAN,
        tool="spec the auth flow",
        role=AgentRole.SPECIFIER,
    )
    walker._runtime = runtime
    result = await walker._execute_subplan(step)
    assert result.status == "completed"
    assert result.metadata["subplan_result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_execute_subplan_returns_failed_when_subplan_aborts():
    registry, runtime = _make_role_registry(
        spawn_return=Plan(plan_id="p_sub", spec="sub"),
        raises=PlanAborted("user pressed x"),
    )
    walker = PlanWalker(
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=registry,
    )
    step = PlanStep(
        id="step-1",
        intent="spec auth flow",
        kind=PlanStepKind.SUBPLAN,
        tool="spec the auth flow",
        role=AgentRole.SPECIFIER,
    )
    walker._runtime = runtime
    result = await walker._execute_subplan(step)
    assert result.status == "failed"
    assert result.metadata["subplan_aborted"] is True


@pytest.mark.asyncio
async def test_execute_subplan_raises_when_registry_missing():
    walker = PlanWalker(
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        role_registry=None,
    )
    step = PlanStep(
        id="step-1",
        intent="spec auth flow",
        kind=PlanStepKind.SUBPLAN,
        tool="spec the auth flow",
        role=AgentRole.SPECIFIER,
    )
    with pytest.raises(RuntimeError, match="RoleRegistry not configured"):
        await walker._execute_subplan(step)


class _StubPipeline:
    def __init__(self, outcome):
        self._outcome = outcome

    async def verify(self, step, step_result, ctx):
        return self._outcome


@pytest.mark.asyncio
async def test_execute_verify_with_pipeline_passes_when_outcome_passed():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register("security", _StubPipeline(VerificationOutcome(passed=True)))
    walker = PlanWalker(
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1",
        intent="verify security",
        kind=PlanStepKind.VERIFY,
        pipeline="security",
        on_failure=OnFailure.ABORT,
    )
    result = await walker._execute_verify(step, StepResult(step_id="step-1", status="completed"))
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
        channel=MagicMock(),
        tools=MagicMock(),
        wal=MagicMock(),
        verifier_adapter=adapter,
    )
    step = PlanStep(
        id="step-1",
        intent="verify security",
        kind=PlanStepKind.VERIFY,
        pipeline="security",
        on_failure=OnFailure.RETRY_WITH_FEEDBACK,
    )
    result = await walker._execute_verify(step, StepResult(step_id="step-1", status="completed"))
    assert result.status == "retry_with_feedback"
    assert "eval() found at auth.py:42" in result.feedback
