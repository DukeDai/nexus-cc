"""Tests for PlanWalker CRITIQUE step execution through injected LLM."""
from __future__ import annotations

import asyncio
import json

import pytest

from src.agent.control import ControlChannel, StepResult
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanWalker, StepFailure


class FakeLLM:
    """A fake LLM that records calls and returns a fixed JSON response."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response or {"passes": True, "feedback": "looks good"}
        self.calls: list[tuple[str, list[dict]]] = []

    async def complete(self, system: str, messages: list[dict]) -> FakeResponse:
        self.calls.append((system, messages))
        return FakeResponse(
            content=[FakeBlock(text=json.dumps(self._response))]
        )


class FakeResponse:
    def __init__(self, content: list) -> None:
        self.content = content


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


def make_channel() -> ControlChannel:
    return ControlChannel()


def make_plan_with_critique_step(
    intent: str = "verify email validation",
    success_criteria: str = "returns 400 on bad email",
    context: str = "",
    on_failure: OnFailure = OnFailure.ASK,
) -> Plan:
    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.CRITIQUE,
        intent=intent,
        args={"context": context},
        success_criteria=success_criteria,
        on_failure=on_failure,
    )
    return Plan(plan_id=new_plan_id(), spec="test plan", steps=[step])


# ─── Tests ─────────────────────────────────────────────────────────────────────


class TestCritiqueStepCallsLLM:
    """CRITIQUE step calls the injected LLM with intent/context/criteria."""

    @pytest.mark.asyncio
    async def test_critique_step_calls_llm(self):
        # Build a Plan with 1 CRITIQUE step
        intent = "verify email validation"
        criteria = "returns 400 on bad email"
        context = "POST /users with body {email: 'bad'} returned 200"
        plan = make_plan_with_critique_step(
            intent=intent,
            success_criteria=criteria,
            context=context,
        )

        # Create a fake LLM
        fake = FakeLLM(response={"passes": True, "feedback": "looks good"})

        # Pass it to PlanWalker
        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=None, llm=fake)

        # Run walk
        results = await walker.walk(plan)

        # Assert LLM was called once
        assert len(fake.calls) == 1
        system, messages = fake.calls[0]

        # Assert system prompt is correct
        assert system == "You critique step outcomes."

        # Assert user message contains intent and criteria
        assert len(messages) == 1
        user_msg = messages[0]["content"]
        assert intent in user_msg
        assert criteria in user_msg

        # Assert step result status is done
        assert len(results) == 1
        assert results[0].status == "done"
        assert results[0].step_id == plan.steps[0].id