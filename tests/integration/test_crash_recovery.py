"""Integration tests for PlanWalker WAL checkpoint integration."""
from __future__ import annotations

import pytest

from src.agent.control import ControlChannel
from src.agent.plan import Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanWalker
from src.context.wal import WALManager
from src.tools.registry import ToolRegistry


class _EchoTool:
    name = "Echo"
    description = "echoes input"
    args_schema = {}

    async def execute(self, **kwargs):
        return {"echoed": kwargs}


@pytest.mark.asyncio
async def test_walker_writes_checkpoint_per_step(tmp_path):
    wal = WALManager(path=tmp_path / "wal.jsonl")
    channel = ControlChannel()
    tools = ToolRegistry()
    tools.register(_EchoTool())
    plan = Plan(
        plan_id=new_plan_id(),
        spec="test",
        steps=[
            PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="step1", tool="Echo", args={"x": 1}),
            PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="step2", tool="Echo", args={"x": 2}),
        ],
    )
    walker = PlanWalker(channel=channel, tools=tools, wal=wal)
    await walker.walk(plan)
    completed = wal.get_completed_step_ids(plan.plan_id)
    assert len(completed) == 2


@pytest.mark.asyncio
async def test_walker_resumes_from_checkpoint_skipping_completed(tmp_path):
    """After a simulated crash, walker resumes and only runs uncompleted steps."""
    wal = WALManager(path=tmp_path / "wal.jsonl")
    channel = ControlChannel()
    tools = ToolRegistry()
    tools.register(_EchoTool())

    plan_id = new_plan_id()
    s1, s2, s3 = new_step_id(), new_step_id(), new_step_id()
    plan = Plan(
        plan_id=plan_id,
        spec="test",
        steps=[
            PlanStep(id=s1, kind=PlanStepKind.TOOL, intent="step1", tool="Echo"),
            PlanStep(id=s2, kind=PlanStepKind.TOOL, intent="step2", tool="Echo"),
            PlanStep(id=s3, kind=PlanStepKind.TOOL, intent="step3", tool="Echo"),
        ],
    )

    # Simulate prior partial execution: s1 already done
    await wal.checkpoint(plan=plan, cursor=s1)

    walker = PlanWalker(channel=channel, tools=tools, wal=wal)
    # Walker should skip s1 (already checkpointed) and execute s2, s3
    await walker.walk(plan)
    completed = wal.get_completed_step_ids(plan.plan_id)
    assert s1 in completed
    assert s2 in completed
    assert s3 in completed
