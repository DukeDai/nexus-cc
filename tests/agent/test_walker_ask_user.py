"""Tests for ASK_USER step blocking behavior in PlanWalker."""
from __future__ import annotations

import asyncio

import pytest

from src.agent.control import Command, CommandKind, ControlChannel, StepResult
from src.agent.events import AskUser
from src.agent.plan import Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanWalker


class EmptyToolRegistry:
    """Minimal tool registry with no tools."""

    async def execute(self, name: str, args: dict) -> str:
        raise RuntimeError(f"No tool named {name}")


def make_plan_with_ask_user_step(question: str, options: list[str]) -> tuple[Plan, PlanStep]:
    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.ASK_USER,
        intent="ask user a question",
        args={"question": question, "options": options},
    )
    plan = Plan(plan_id=new_plan_id(), spec="test ask_user plan", steps=[step])
    return plan, step


@pytest.mark.asyncio
async def test_ask_user_blocks_until_answer():
    """ASK_USER step blocks walker until ANSWER_QUESTION command is sent."""
    question_text = "Which database?"
    options = ["postgres", "sqlite"]
    plan, step = make_plan_with_ask_user_step(question_text, options)

    channel = ControlChannel()
    tools = EmptyToolRegistry()
    walker = PlanWalker(channel=channel, tools=tools)

    # Start walker in background task
    walk_task = asyncio.create_task(walker.walk(plan))

    # Wait briefly — walker should be blocked on recv_command, not done
    await asyncio.sleep(0.05)
    assert not walk_task.done(), "Walker should be blocked on ASK_USER step, not yet done"

    # Send ANSWER_QUESTION command to unblock the walker
    await channel.send_command(Command(
        kind=CommandKind.ANSWER_QUESTION,
        payload={"step_id": step.id, "answer": "postgres"},
    ))

    # Wait for walker to complete
    await asyncio.sleep(0.1)
    assert walk_task.done(), "Walker should be done after receiving ANSWER_QUESTION"

    # Verify step result
    results = await walk_task
    assert len(results) == 1
    assert results[0].output == "postgres"
    assert results[0].status == "done"

    # Drain events and verify AskUser was emitted
    events = []
    while True:
        evt = channel.try_recv_event()
        if evt is None:
            break
        events.append(evt)

    ask_user_events = [e for e in events if isinstance(e, AskUser)]
    assert len(ask_user_events) == 1, f"Expected 1 AskUser event, got {len(ask_user_events)}: {events}"
    assert ask_user_events[0].question == question_text
    assert ask_user_events[0].options == options
    assert ask_user_events[0].step.id == step.id