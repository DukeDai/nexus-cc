"""Tests for PlanWalker CRITIQUE step model_hint propagation to the ModelRouter.

These tests pin the v1.2 contract: when the router is enabled, _execute_critique_step
forwards ModelHint.CRITIQUE to the underlying LLM client (consumed by _RouterAdapter)
so the router can pick the right model for the critique call. The default is
ModelHint.CRITIQUE — the only LLM call path in walker.py.

When NEXUS_USE_MODEL_ROUTER is unset (legacy LLMClient), model_hint is ignored
by the client but is still forwarded as a kwarg — behavior is unchanged.
"""
from __future__ import annotations

import json

import pytest

from src.agent.control import ControlChannel
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanWalker
from src.llm.model_policy import ModelHint


class HintCapturingLLM:
    """Records (system, messages, kwargs) for each .complete() call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, *, system: str, messages: list[dict], **kwargs):
        self.calls.append({"system": system, "messages": messages, "kwargs": dict(kwargs)})
        return _CritiqueResponse(
            content=[_CritiqueBlock(text=json.dumps({"passes": True, "feedback": "ok"}))]
        )


class _CritiqueResponse:
    def __init__(self, content: list) -> None:
        self.content = content


class _CritiqueBlock:
    def __init__(self, text: str) -> None:
        self.text = text


def make_channel() -> ControlChannel:
    return ControlChannel()


def make_plan_with_critique_step(
    intent: str = "verify email validation",
    success_criteria: str = "returns 400 on bad email",
) -> Plan:
    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.CRITIQUE,
        intent=intent,
        args={"context": ""},
        success_criteria=success_criteria,
        on_failure=OnFailure.ASK,
    )
    return Plan(plan_id=new_plan_id(), spec="test plan", steps=[step])


@pytest.mark.asyncio
async def test_execute_critique_step_routes_with_critique_hint():
    """_execute_critique_step forwards ModelHint.CRITIQUE to the LLM client."""
    plan = make_plan_with_critique_step()
    fake = HintCapturingLLM()
    channel = make_channel()
    walker = PlanWalker(channel=channel, tools=None, llm=fake)

    results = await walker.walk(plan)

    assert len(results) == 1
    assert results[0].status == "done"
    assert len(fake.calls) == 1
    assert fake.calls[0]["kwargs"].get("model_hint") is ModelHint.CRITIQUE


@pytest.mark.asyncio
async def test_execute_critique_step_default_hint_is_critique():
    """_execute_critique_step with no model_hint argument still forwards CRITIQUE.

    This pins the v1.2 default: critique steps always route under ModelHint.CRITIQUE
    unless a caller overrides it explicitly.
    """
    plan = make_plan_with_critique_step()
    fake = HintCapturingLLM()
    channel = make_channel()
    walker = PlanWalker(channel=channel, tools=None, llm=fake)

    # Invoke the step handler directly — no model_hint argument.
    step = plan.steps[0]
    result = await walker._execute_critique_step(step)

    assert result.status == "done"
    assert len(fake.calls) == 1
    assert fake.calls[0]["kwargs"].get("model_hint") is ModelHint.CRITIQUE
