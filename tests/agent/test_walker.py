"""Tests for PlanWalker - walks Plan.steps[] emitting events via ControlChannel."""
from __future__ import annotations

import asyncio

import pytest

from src.agent.control import Command, CommandKind, ControlChannel, StepResult
from src.agent.events import (
    AskUser,
    PlanCompleted,
    StepCompleted,
    StepFailed,
    StepStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanAborted, PlanWalker


# ─── Fake Tool helpers ────────────────────────────────────────────────────────


class FakeTool:
    """A fake tool that returns a fixed result."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self.name = name
        self._result = result

    async def execute(self, **kwargs) -> str:
        return self._result


class FlakyTool:
    """A tool that fails on first call, succeeds on second."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self.name = name
        self._result = result
        self._call_count = 0

    async def execute(self, **kwargs) -> str:
        self._call_count += 1
        if self._call_count == 1:
            raise RuntimeError(f"{self.name} failed on first attempt")
        return self._result


class RaisingTool:
    """A tool that always raises."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def execute(self, **kwargs) -> str:
        raise RuntimeError(f"{self.name} always fails")


class FakeToolRegistry:
    """A minimal ToolRegistry lookalike that wraps individual tool objects.

    Satisfies the interface PlanWalker expects: execute(name, args) → tool.execute(**args).
    """

    def __init__(self, tools: list[FakeTool | FlakyTool | RaisingTool]) -> None:
        self._tools = {t.name: t for t in tools}

    async def execute(self, name: str, args: dict) -> str:
        tool = self._tools[name]
        return await tool.execute(**args)


# ─── Test fixtures ─────────────────────────────────────────────────────────────


def make_channel() -> ControlChannel:
    return ControlChannel()


def make_tools(fakes: list[FakeTool | FlakyTool | RaisingTool]) -> FakeToolRegistry:
    """Return a FakeToolRegistry backed by the given fake tools."""
    return FakeToolRegistry(fakes)


def make_plan_with_steps(steps: list[PlanStep]) -> Plan:
    return Plan(plan_id=new_plan_id(), spec="test plan", steps=steps)


# ─── Tests ─────────────────────────────────────────────────────────────────────


class TestToolStepsExecuteInOrderAndEmitEvents:
    """Verify tool steps execute in order and events emit in sequence."""

    @pytest.mark.asyncio
    async def test_tool_steps_execute_in_order_and_emit_events(self):
        # Build 3 TOOL steps, each with a distinct fake tool
        tool1 = FakeTool("tool_alpha", result="result_alpha")
        tool2 = FakeTool("tool_beta", result="result_beta")
        tool3 = FakeTool("tool_gamma", result="result_gamma")

        tools_registry = make_tools([tool1, tool2, tool3])

        steps = [
            PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="run tool_alpha", tool="tool_alpha", args={}),
            PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="run tool_beta", tool="tool_beta", args={}),
            PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="run tool_gamma", tool="tool_gamma", args={}),
        ]
        plan = make_plan_with_steps(steps)

        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=tools_registry)

        # Run walker
        results = await walker.walk(plan)

        # Drain all emitted events
        events = []
        while True:
            evt = channel.try_recv_event()
            if evt is None:
                break
            events.append(evt)

        # Expected event sequence:
        # StepStarted(0/3), ToolCallStarted, ToolCallCompleted, StepCompleted(0),
        # StepStarted(1/3), ToolCallStarted, ToolCallCompleted, StepCompleted(1),
        # StepStarted(2/3), ToolCallStarted, ToolCallCompleted, StepCompleted(2),
        # PlanCompleted
        expected_sequence = [
            StepStarted,    # step 0
            ToolCallStarted,
            ToolCallCompleted,
            StepCompleted,  # step 0 done
            StepStarted,    # step 1
            ToolCallStarted,
            ToolCallCompleted,
            StepCompleted,  # step 1 done
            StepStarted,    # step 2
            ToolCallStarted,
            ToolCallCompleted,
            StepCompleted,  # step 2 done
            PlanCompleted,
        ]

        assert len(events) == len(expected_sequence), f"Expected {len(expected_sequence)} events, got {len(events)}: {events}"

        for idx, (evt, expected_cls) in enumerate(zip(events, expected_sequence)):
            assert isinstance(evt, expected_cls), f"event[{idx}] is {type(evt).__name__}, expected {expected_cls.__name__}"

        # Verify results
        assert len(results) == 3
        assert results[0].status == "done"
        assert results[0].output == "result_alpha"
        assert results[1].status == "done"
        assert results[1].output == "result_beta"
        assert results[2].status == "done"
        assert results[2].output == "result_gamma"

        # PlanCompleted should be last
        assert isinstance(events[-1], PlanCompleted)
        assert len(events[-1].results) == 3


class TestStepFailureSkipStrategy:
    """A TOOL step with on_failure=SKIP whose tool raises → StepResult status=skipped."""

    @pytest.mark.asyncio
    async def test_step_failure_skip_strategy(self):
        raising = RaisingTool("always_fail")
        tools_registry = make_tools([raising])

        steps = [
            PlanStep(
                id=new_step_id(),
                kind=PlanStepKind.TOOL,
                intent="this will fail",
                tool="always_fail",
                args={},
                on_failure=OnFailure.SKIP,
            ),
        ]
        plan = make_plan_with_steps(steps)

        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=tools_registry)

        results = await walker.walk(plan)

        assert len(results) == 1
        assert results[0].status == "skipped"
        assert results[0].error is not None

        # Drain events and verify StepFailed was emitted before StepCompleted.
        # Spec section 4.2: walker MUST emit StepFailed when a step does not complete.
        # The TUI marks the step ✗ even though walker records status=skipped.
        events = []
        while True:
            evt = channel.try_recv_event()
            if evt is None:
                break
            events.append(evt)

        step_failed_events = [e for e in events if isinstance(e, StepFailed)]
        step_completed_events = [e for e in events if isinstance(e, StepCompleted)]
        assert len(step_failed_events) == 1, f"expected exactly one StepFailed, got {[type(e).__name__ for e in events]}"
        assert len(step_completed_events) == 1, f"expected exactly one StepCompleted, got {[type(e).__name__ for e in events]}"
        assert step_failed_events[0].step.id == steps[0].id
        assert step_completed_events[0].result.status == "skipped"


class TestStepFailureAbortStrategy:
    """A TOOL step with on_failure=ABORT whose tool raises → PlanAborted exception."""

    @pytest.mark.asyncio
    async def test_step_failure_abort_strategy(self):
        raising = RaisingTool("always_fail")
        tools_registry = make_tools([raising])

        steps = [
            PlanStep(
                id=new_step_id(),
                kind=PlanStepKind.TOOL,
                intent="this will abort",
                tool="always_fail",
                args={},
                on_failure=OnFailure.ABORT,
            ),
        ]
        plan = make_plan_with_steps(steps)

        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=tools_registry)

        # Spec section 4.2: StepFailed MUST be emitted before PlanAborted is raised.
        # Drain events before the exception propagates out of walk().
        events_captured: list = []

        async def drain_while_walking():
            try:
                await walker.walk(plan)
            except PlanAborted:
                pass
            # Drain remaining events after walk completes (exception or not)
            while True:
                evt = channel.try_recv_event()
                if evt is None:
                    break
                events_captured.append(evt)

        await drain_while_walking()

        step_failed_events = [e for e in events_captured if isinstance(e, StepFailed)]
        assert len(step_failed_events) == 1, f"expected exactly one StepFailed, got {[type(e).__name__ for e in events_captured]}"
        assert step_failed_events[0].step.id == steps[0].id


class TestStepFailureRetryThenSucceed:
    """A FlakyTool fails once then succeeds, with on_failure=RETRY → StepResult status=done."""

    @pytest.mark.asyncio
    async def test_step_failure_retry_then_succeed(self):
        flaky = FlakyTool("flaky_tool", result="finally_ok")
        tools_registry = make_tools([flaky])

        steps = [
            PlanStep(
                id=new_step_id(),
                kind=PlanStepKind.TOOL,
                intent="flaky tool",
                tool="flaky_tool",
                args={},
                on_failure=OnFailure.RETRY,
            ),
        ]
        plan = make_plan_with_steps(steps)

        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=tools_registry)

        results = await walker.walk(plan)

        assert len(results) == 1
        assert results[0].status == "done"
        assert results[0].output == "finally_ok"

        # FlakyTool fails on its 1st call, succeeds on its 2nd.
        # StepFailure is re-raised from execute_step's retry loop (not retried internally);
        # _handle_step_failure(RETRY) re-calls execute_step → walk-level retry.
        # So attempt 0: ToolCallStarted, raises StepFailure → walk catches → retry.
        # attempt 1: ToolCallStarted, succeeds → ToolCallCompleted, return.
        events = []
        while True:
            evt = channel.try_recv_event()
            if evt is None:
                break
            events.append(evt)

        tool_started = [e for e in events if isinstance(e, ToolCallStarted)]
        tool_completed = [e for e in events if isinstance(e, ToolCallCompleted)]
        assert len(tool_started) == 2   # two execute_step calls (initial + one retry)
        assert len(tool_completed) == 1  # only the final successful attempt emits this
