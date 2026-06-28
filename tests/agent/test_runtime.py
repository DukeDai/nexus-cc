"""Tests for AgentRuntime - orchestrates Planner + Walker + WAL."""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from src.agent.control import CommandKind, ControlChannel, StepResult
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id


# ─── Fake helpers ──────────────────────────────────────────────────────────────


class FakeLLM:
    """Fake LLM that returns a predefined JSON plan response."""

    def __init__(self, plan_json: str) -> None:
        self._plan_json = plan_json

    async def complete(self, *, system: str, messages: list[dict], **kwargs) -> MagicMock:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=self._plan_json)]
        return mock_response


class EmptyWAL:
    """Minimal WAL that does nothing."""

    async def checkpoint(self, *, plan, cursor: str, result: dict | None = None) -> None:
        pass

    def get_completed_step_ids(self, plan_id: str) -> set[str]:
        return set()


# ─── Tests ─────────────────────────────────────────────────────────────────────


class TestPlanThenWalk:
    """Verify AgentRuntime.plan() then .walk() executes all steps."""

    @pytest.mark.asyncio
    async def test_plan_then_walk(self):
        plan_json = """{
  "spec": "Add type hints",
  "assumptions": [],
  "risks": [],
  "steps": [
    {
      "id": "step_11111111",
      "kind": "TOOL",
      "intent": "Run mypy",
      "tool": "bash",
      "args": {"cmd": "echo ok"},
      "success_criteria": "mypy passed",
      "on_failure": "abort",
      "timeout_s": 30
    },
    {
      "id": "step_22222222",
      "kind": "TOOL",
      "intent": "Format with ruff",
      "tool": "bash",
      "args": {"cmd": "echo ok"},
      "success_criteria": "ruff passed",
      "on_failure": "abort",
      "timeout_s": 30
    }
  ]
}"""
        fake_llm = FakeLLM(plan_json)

        # Fake tool registry that returns "ok" for any tool
        class FakeToolRegistry:
            async def execute(self, name: str, args: dict) -> str:
                return "ok"

        channel = ControlChannel()
        from src.agent.runtime import AgentRuntime

        runtime = AgentRuntime(
            llm=fake_llm,
            tools=FakeToolRegistry(),
            verification=None,
            wal=EmptyWAL(),
            channel=channel,
        )

        # Plan
        plan = await runtime.plan("add type hints")
        assert plan.spec == "Add type hints"
        assert len(plan.steps) == 2

        # Walk
        results = await runtime.walk(plan)
        assert len(results) == len(plan.steps)
        for r in results:
            assert r.status == "done"


class TestEditStepBumpsVersion:
    """Verify edit_step increments Plan.version."""

    @pytest.mark.asyncio
    async def test_edit_step_bumps_version(self):
        plan_json = """{
  "spec": "Test edit",
  "assumptions": [],
  "risks": [],
  "steps": [
    {
      "id": "step_aaa11111",
      "kind": "TOOL",
      "intent": "Step one",
      "tool": "bash",
      "args": {},
      "success_criteria": "done",
      "on_failure": "ask",
      "timeout_s": 30
    },
    {
      "id": "step_bbb22222",
      "kind": "TOOL",
      "intent": "Step two",
      "tool": "bash",
      "args": {},
      "success_criteria": "done",
      "on_failure": "ask",
      "timeout_s": 30
    }
  ]
}"""
        fake_llm = FakeLLM(plan_json)

        class FakeToolRegistry:
            async def execute(self, name: str, args: dict) -> str:
                return "ok"

        channel = ControlChannel()
        from src.agent.runtime import AgentRuntime

        runtime = AgentRuntime(
            llm=fake_llm,
            tools=FakeToolRegistry(),
            verification=None,
            wal=EmptyWAL(),
            channel=channel,
        )

        plan = await runtime.plan("test edit")
        assert plan.version == 1

        new_step = PlanStep(
            id="step_aaa11111",
            kind=PlanStepKind.TOOL,
            intent="Step one edited",
            tool="bash",
            args={"cmd": "echo edited"},
            success_criteria="done",
            on_failure=OnFailure.ASK,
            timeout_s=30,
        )
        runtime.edit_step(plan.steps[0].id, new_step)
        assert plan.version == 2


class TestPlanRequiresLLM:
    """Verify AgentRuntime raises when constructed without LLM."""

    @pytest.mark.asyncio
    async def test_plan_requires_llm(self):
        channel = ControlChannel()

        class FakeToolRegistry:
            async def execute(self, name: str, args: dict) -> str:
                return "ok"

        from src.agent.runtime import AgentRuntime

        runtime = AgentRuntime(
            llm=None,
            tools=FakeToolRegistry(),
            verification=None,
            wal=EmptyWAL(),
            channel=channel,
        )

        with pytest.raises(RuntimeError, match="Planner requires LLM client"):
            await runtime.plan("test")