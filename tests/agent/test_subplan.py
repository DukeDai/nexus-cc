import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.control import StepResult
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agent.walker import PlanWalker, PlanAborted
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry


def _make_role_registry(spawn_return: Plan, *, raises: Exception | None = None) -> RoleRegistry:
    runtime = MagicMock()
    runtime.plan_subplan = MagicMock(side_effect=raises) if raises else MagicMock(return_value=spawn_return)
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
        plan=Plan(plan_id="p_parent", spec="parent", steps=[]),
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
        plan=Plan(plan_id="p_parent", spec="parent", steps=[]),
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
        plan=Plan(plan_id="p_parent", spec="parent", steps=[]),
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
