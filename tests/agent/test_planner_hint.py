"""Tests for Planner.plan() model_hint propagation to the ModelRouter.

These tests pin the v1.2 contract: when the router is enabled, Planner.plan()
forwards a ModelHint to the underlying LLM client (consumed by _RouterAdapter)
so the router can pick the right model per call. The default is PLANNER;
critique sub-plan callers pass ModelHint.CRITIQUE explicitly.

When NEXUS_USE_MODEL_ROUTER is unset (legacy LLMClient), model_hint is ignored
by the client but is still forwarded as a kwarg — behavior is unchanged.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.llm.model_policy import ModelHint


class HintCapturingLLM:
    """Records (system, messages, kwargs) for each .complete() call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, *, system: str, messages: list[dict], **kwargs):
        self.calls.append({"system": system, "messages": messages, "kwargs": dict(kwargs)})
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"spec":"t","assumptions":[],"risks":[],"steps":[]}')]
        return mock_response


def _valid_plan_json() -> str:
    return (
        '{"spec":"t","assumptions":[],"risks":[],'
        '"steps":[{"id":"step_aaaaaaaa","kind":"TOOL","intent":"do it",'
        '"tool":"bash","args":{"cmd":"echo hi"},"success_criteria":"printed",'
        '"on_failure":"ask","timeout_s":30}]}'
    )


@pytest.mark.asyncio
async def test_plan_forwards_planner_hint_by_default():
    """Planner.plan() with no model_hint forwards ModelHint.PLANNER to the LLM."""
    from agent.planner import Planner

    llm = HintCapturingLLM()
    planner = Planner(llm=llm)
    await planner.plan("do a thing")
    assert len(llm.calls) == 1
    assert llm.calls[0]["kwargs"].get("model_hint") is ModelHint.PLANNER


@pytest.mark.asyncio
async def test_plan_explicit_planner_hint_is_propagated():
    """Planner.plan(model_hint=PLANNER) forwards ModelHint.PLANNER explicitly."""
    from agent.planner import Planner

    llm = HintCapturingLLM()
    planner = Planner(llm=llm)
    await planner.plan("do a thing", model_hint=ModelHint.PLANNER)
    assert llm.calls[0]["kwargs"].get("model_hint") is ModelHint.PLANNER


@pytest.mark.asyncio
async def test_plan_critique_hint_is_propagated_for_subplans():
    """Planner.plan(model_hint=CRITIQUE) forwards ModelHint.CRITIQUE — used for sub-plans."""
    from agent.planner import Planner

    llm = HintCapturingLLM()
    planner = Planner(llm=llm)
    # Simulate a CRITIQUE sub-plan call site (e.g. runtime.plan_subplan).
    await planner.plan(
        "review the diff",
        spec="Role: critique",
        model_hint=ModelHint.CRITIQUE,
    )
    assert llm.calls[0]["kwargs"].get("model_hint") is ModelHint.CRITIQUE


@pytest.mark.asyncio
async def test_router_adapter_consumes_hint_from_planner():
    """End-to-end: Planner.plan(hint=X) → _RouterAdapter routes with hint=X → ModelRouter.route(hint=X)."""
    from src.cli.commands.run import _RouterAdapter

    class FakeRouter:
        def __init__(self):
            self.last_hint = None
            self.last_role = None
            self.last_messages = None

        def route(self, *, messages, hint, role=None, **kwargs):
            self.last_hint = hint
            self.last_role = role
            self.last_messages = messages
            resp = MagicMock()
            resp.content = [MagicMock(text=_valid_plan_json())]
            resp.usage = None  # skip cost emission
            return ("claude-test", resp)

    from agent.planner import Planner

    fake_router = FakeRouter()
    adapter = _RouterAdapter(router=fake_router, hint=ModelHint.PLANNER)
    planner = Planner(llm=adapter)

    await planner.plan("critique the work", model_hint=ModelHint.CRITIQUE)

    assert fake_router.last_hint is ModelHint.CRITIQUE
    # Sanity: adapter's own default was PLANNER but per-call hint overrode it.
    assert adapter._hint is ModelHint.PLANNER