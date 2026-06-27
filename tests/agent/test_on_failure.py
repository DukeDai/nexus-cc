"""Comprehensive on_failure strategy tests using FlakyTool."""
from __future__ import annotations

import asyncio

import pytest

from src.agent.control import Command, CommandKind, ControlChannel, StepResult
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanAborted, PlanWalker
from src.tools.registry import ToolRegistry


class FlakyTool:
    """Tool that fails the first N invocations, then succeeds."""

    name = "Flaky"
    description = "Fails first N times, then succeeds"
    args_schema: dict = {}

    def __init__(self, fail_count: int) -> None:
        self._fail_count = fail_count
        self._attempts = 0

    async def execute(self, **kwargs) -> dict:
        self._attempts += 1
        if self._attempts <= self._fail_count:
            raise RuntimeError(f"flaky failure attempt {self._attempts}")
        return {"succeeded_after": self._attempts}

    @property
    def attempts(self) -> int:
        return self._attempts


@pytest.mark.asyncio
async def test_skip_strategy_returns_skipped_result():
    """on_failure=SKIP returns StepResult with status='skipped'."""
    channel = ControlChannel()
    tools = ToolRegistry()
    flaky = FlakyTool(fail_count=100)  # always fails
    tools.register(flaky)

    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.TOOL,
        intent="step1",
        tool="Flaky",
        on_failure=OnFailure.SKIP,
    )
    plan = Plan(plan_id=new_plan_id(), spec="test", steps=[step])
    walker = PlanWalker(channel=channel, tools=tools)
    results = await walker.walk(plan)

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert results[0].error is not None


@pytest.mark.asyncio
async def test_abort_strategy_raises_plan_aborted():
    """on_failure=ABORT raises PlanAborted after retries exhausted."""
    channel = ControlChannel()
    tools = ToolRegistry()
    flaky = FlakyTool(fail_count=100)  # always fails
    tools.register(flaky)

    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.TOOL,
        intent="step1",
        tool="Flaky",
        on_failure=OnFailure.ABORT,
    )
    plan = Plan(plan_id=new_plan_id(), spec="test", steps=[step])
    walker = PlanWalker(channel=channel, tools=tools)

    with pytest.raises(PlanAborted):
        await walker.walk(plan)


@pytest.mark.asyncio
async def test_ask_strategy_with_skip_answer_returns_skipped():
    """on_failure=ASK + ANSWER_QUESTION 'skip' → returns skipped result."""
    channel = ControlChannel()
    tools = ToolRegistry()
    flaky = FlakyTool(fail_count=100)
    tools.register(flaky)

    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.TOOL,
        intent="step1",
        tool="Flaky",
        on_failure=OnFailure.ASK,
    )
    plan = Plan(plan_id=new_plan_id(), spec="test", steps=[step])
    walker = PlanWalker(channel=channel, tools=tools)

    async def send_skip() -> None:
        await asyncio.sleep(0.05)  # let walker ask first
        await channel.send_command(Command(
            kind=CommandKind.ANSWER_QUESTION,
            payload={"step_id": step.id, "answer": "skip"},
        ))

    asyncio.create_task(send_skip())

    results = await walker.walk(plan)

    assert len(results) == 1
    assert results[0].status == "skipped"


@pytest.mark.asyncio
async def test_retry_then_succeed():
    """on_failure=RETRY with FlakyTool(fail_count=1) succeeds on 2nd attempt."""
    channel = ControlChannel()
    tools = ToolRegistry()
    flaky = FlakyTool(fail_count=1)  # fails once, succeeds on attempt 2
    tools.register(flaky)

    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.TOOL,
        intent="step1",
        tool="Flaky",
        on_failure=OnFailure.RETRY,
    )
    plan = Plan(plan_id=new_plan_id(), spec="test", steps=[step])
    walker = PlanWalker(channel=channel, tools=tools)
    results = await walker.walk(plan)

    assert len(results) == 1
    assert results[0].status == "done"


@pytest.mark.asyncio
async def test_ask_strategy_with_retry_answer_retries():
    """on_failure=ASK + ANSWER_QUESTION 'retry' → retries the step."""
    channel = ControlChannel()
    tools = ToolRegistry()
    # Use FlakyTool that always fails so the retry path inside the walker
    # also fails — but answer=retry still completes via the walker's
    # own _handle_step_failure retry which re-calls execute_step.
    # We expect status=done because the FlakyTool eventually succeeds.
    flaky = FlakyTool(fail_count=2)  # fails twice, succeeds on 3rd attempt
    tools.register(flaky)

    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.TOOL,
        intent="step1",
        tool="Flaky",
        on_failure=OnFailure.ASK,
    )
    plan = Plan(plan_id=new_plan_id(), spec="test", steps=[step])
    walker = PlanWalker(channel=channel, tools=tools)

    async def send_retry() -> None:
        await asyncio.sleep(0.05)
        await channel.send_command(Command(
            kind=CommandKind.ANSWER_QUESTION,
            payload={"step_id": step.id, "answer": "retry"},
        ))

    asyncio.create_task(send_retry())
    results = await walker.walk(plan)

    assert len(results) == 1
    assert results[0].status == "done"