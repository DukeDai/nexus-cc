"""Tests for PlanWalker pause/resume at step boundaries with Paused/Resumed events."""
from __future__ import annotations

import asyncio

import pytest

from src.agent.control import ControlChannel
from src.agent.events import Paused, PlanCompleted, Resumed, StepCompleted, StepStarted, ToolCallCompleted, ToolCallStarted
from src.agent.plan import Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanWalker


class FakeTool:
    """A fake tool that returns a fixed result."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self.name = name
        self._result = result

    async def execute(self, **kwargs) -> str:
        return self._result


class FakeToolRegistry:
    """Minimal tool registry for tests."""

    def __init__(self, tools: list[FakeTool]) -> None:
        self._tools = {t.name: t for t in tools}

    async def execute(self, name: str, args: dict) -> str:
        tool = self._tools[name]
        return await tool.execute(**args)


def make_plan_with_steps(steps: list[PlanStep]) -> Plan:
    return Plan(plan_id=new_plan_id(), spec="test plan", steps=steps)


@pytest.mark.asyncio
async def test_pause_emits_paused_and_resumed_events():
    """When channel is paused before walk starts, walker emits Paused then Resumed around step boundary."""
    # Build a plan with 2 TOOL steps
    tool1 = FakeTool("tool_alpha", result="result_alpha")
    tool2 = FakeTool("tool_beta", result="result_beta")
    tools_registry = FakeToolRegistry([tool1, tool2])

    step0 = PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="run tool_alpha", tool="tool_alpha", args={})
    step1 = PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="run tool_beta", tool="tool_beta", args={})
    plan = make_plan_with_steps([step0, step1])

    # Pause channel BEFORE walking — walker should block at first step boundary
    channel = ControlChannel()
    channel.pause()

    walker = PlanWalker(channel=channel, tools=tools_registry)

    # Start walker in background task
    walk_task = asyncio.create_task(walker.walk(plan))

    # Give walker a moment to hit the pause point
    await asyncio.sleep(0.05)

    # Drain events: should see Paused(step_id=step0.id) first
    events = []
    while True:
        evt = channel.try_recv_event()
        if evt is None:
            break
        events.append(evt)

    assert len(events) >= 1, f"Expected at least Paused event, got: {events}"
    assert isinstance(events[0], Paused), f"First event should be Paused, got: {type(events[0]).__name__}"
    assert events[0].step_id == step0.id, f"Paused.step_id should be step0.id, got: {events[0].step_id}"

    # Resume — walker should unblock and continue
    channel.resume()

    # Wait for walker to finish
    await asyncio.sleep(0.1)

    # Drain remaining events
    while True:
        evt = channel.try_recv_event()
        if evt is None:
            break
        events.append(evt)

    # Verify event sequence: Paused, Resumed, step0 events..., step1 events..., PlanCompleted
    assert isinstance(events[0], Paused), f"First event must be Paused, got: {events[0]}"
    assert events[0].step_id == step0.id

    # Find Resumed — must appear AFTER Paused and BEFORE step0's StepStarted
    resumed_indices = [i for i, e in enumerate(events) if isinstance(e, Resumed)]
    assert len(resumed_indices) == 1, f"Expected exactly one Resumed event, got {len(resumed_indices)} in {events}"
    resumed_idx = resumed_indices[0]
    assert resumed_idx < events.index(next(e for e in events if isinstance(e, StepStarted)))

    # Verify step0 events after Resumed
    step0_started_idx = events.index(next(e for e in events if isinstance(e, StepStarted)))
    assert isinstance(events[step0_started_idx], StepStarted)
    assert events[step0_started_idx].step.id == step0.id

    # Verify both steps completed
    step_completed = [e for e in events if isinstance(e, StepCompleted)]
    assert len(step_completed) == 2, f"Expected 2 StepCompleted events, got {len(step_completed)}"

    # PlanCompleted should be last
    assert isinstance(events[-1], PlanCompleted), f"Last event should be PlanCompleted, got: {type(events[-1]).__name__}"

    # Verify walk completed successfully
    assert walk_task.done(), "Walker should have completed"
    results = await walk_task
    assert len(results) == 2
    assert results[0].output == "result_alpha"
    assert results[1].output == "result_beta"