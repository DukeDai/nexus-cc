"""Tests for Planner memory_context injection."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.planner import Planner


class FakeLLM:
    """Fake LLM that records system prompt."""

    def __init__(self) -> None:
        self.last_system: str = ""

    async def complete(self, *, system: str, messages: list[dict], **kwargs) -> MagicMock:
        self.last_system = system
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"spec":"t","assumptions":[],"risks":[],"steps":[]}')]
        return mock_response


@pytest.mark.asyncio
async def test_planner_accepts_memory_context_in_plan_call():
    """Planner.plan() prepends memory_context to system prompt."""
    fake_llm = FakeLLM()
    planner = Planner(llm=fake_llm)
    await planner.plan(
        "add X",
        memory_context="# Past similar tasks\n- p_abc: success in 12s",
    )
    assert "# Past similar tasks" in fake_llm.last_system
    assert "- p_abc: success in 12s" in fake_llm.last_system


@pytest.mark.asyncio
async def test_planner_works_without_memory_context():
    """Planner.plan() works normally when memory_context is empty."""
    fake_llm = FakeLLM()
    planner = Planner(llm=fake_llm)
    plan = await planner.plan("simple task")
    assert plan.spec == "t"
    assert "# Past similar tasks" not in fake_llm.last_system
