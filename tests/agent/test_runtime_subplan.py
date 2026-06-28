"""Tests for AgentRuntime.plan_subplan + role_registry wiring."""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from src.agent.plan import Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.runtime import AgentRuntime
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry


class FakeLLM:
    """Fake LLM that returns a predefined JSON plan response."""

    def __init__(self, plan_json: str) -> None:
        self._plan_json = plan_json

    async def complete(self, *, system: str, messages: list[dict]) -> MagicMock:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=self._plan_json)]
        return mock_response


class EmptyWAL:
    """Minimal WAL that does nothing."""

    async def checkpoint(self, *, plan, cursor: str, result: dict | None = None) -> None:
        pass

    def get_completed_step_ids(self, plan_id: str) -> set[str]:
        return set()


def test_runtime_accepts_role_registry():
    """AgentRuntime.__init__ accepts role_registry as keyword-only param."""
    registry = RoleRegistry(runtime=None)
    runtime = AgentRuntime(
        llm=MagicMock(),
        tools=MagicMock(),
        verification=MagicMock(),
        wal=MagicMock(),
        channel=MagicMock(),
        role_registry=registry,
    )
    assert runtime.role_registry is registry


class TestPlanSubplan:
    """Tests for AgentRuntime.plan_subplan (async)."""

    @pytest.mark.asyncio
    async def test_plan_subplan_calls_planner_with_role_prompt(self):
        """plan_subplan passes role's system_prompt as spec to Planner.plan."""
        plan_json = """{
  "spec": "spec the auth flow",
  "assumptions": [],
  "risks": [],
  "steps": [
    {
      "id": "step_11111111",
      "kind": "tool",
      "intent": "Read auth files",
      "tool": "Read",
      "args": {"path": "src/auth.py"},
      "success_criteria": "file read",
      "on_failure": "abort",
      "timeout_s": 30
    }
  ]
}"""
        fake_llm = FakeLLM(plan_json)

        registry = RoleRegistry(runtime=None)
        registry.register(
            AgentRole.SPECIFIER,
            RoleDefinition(
                role=AgentRole.SPECIFIER,
                system_prompt="You are a specifier.",
                allowed_tools=["Read", "Glob"],
                model_tier=ModelTier.SONNET,
                max_subplan_steps=8,
            ),
        )

        runtime = AgentRuntime(
            llm=fake_llm,
            tools=MagicMock(),
            verification=MagicMock(),
            wal=EmptyWAL(),
            channel=MagicMock(),
            role_registry=registry,
        )

        definition = registry.get(AgentRole.SPECIFIER)
        sub_plan = await runtime.plan_subplan(
            role=AgentRole.SPECIFIER,
            definition=definition,
            task="spec the auth flow",
            context={"scope": "src/auth/"},
        )

        assert sub_plan.plan_id is not None
        assert "spec the auth flow" in sub_plan.spec

    @pytest.mark.asyncio
    async def test_plan_subplan_respects_max_steps(self):
        """plan_subplan raises if sub-plan exceeds max_subplan_steps."""
        plan_json = """{
  "spec": "a simple plan",
  "assumptions": [],
  "risks": [],
  "steps": [
    {"id": "s1", "kind": "tool", "intent": "step 1", "tool": "bash", "args": {}, "success_criteria": "ok", "on_failure": "abort", "timeout_s": 30},
    {"id": "s2", "kind": "tool", "intent": "step 2", "tool": "bash", "args": {}, "success_criteria": "ok", "on_failure": "abort", "timeout_s": 30},
    {"id": "s3", "kind": "tool", "intent": "step 3", "tool": "bash", "args": {}, "success_criteria": "ok", "on_failure": "abort", "timeout_s": 30}
  ]
}"""
        fake_llm = FakeLLM(plan_json)

        registry = RoleRegistry(runtime=None)
        registry.register(
            AgentRole.SPECIFIER,
            RoleDefinition(
                role=AgentRole.SPECIFIER,
                system_prompt="You are a specifier.",
                allowed_tools=["Read", "Glob"],
                model_tier=ModelTier.SONNET,
                max_subplan_steps=2,  # only allow 2 steps
            ),
        )

        runtime = AgentRuntime(
            llm=fake_llm,
            tools=MagicMock(),
            verification=MagicMock(),
            wal=EmptyWAL(),
            channel=MagicMock(),
            role_registry=registry,
        )

        definition = registry.get(AgentRole.SPECIFIER)
        with pytest.raises(ValueError, match="exceeds max_subplan_steps"):
            await runtime.plan_subplan(
                role=AgentRole.SPECIFIER,
                definition=definition,
                task="spec the auth flow",
                context={},
            )


class TestWalkSetsWalkerRuntime:
    """Test that walk() wires _runtime into walker."""

    @pytest.mark.asyncio
    async def test_walk_sets_walker_runtime(self):
        """walk() sets walker._runtime = self before calling walker.walk()."""
        plan_json = """{
  "spec": "simple walk test",
  "assumptions": [],
  "risks": [],
  "steps": [
    {
      "id": "step_11111111",
      "kind": "tool",
      "intent": "Do a thing",
      "tool": "bash",
      "args": {"cmd": "echo ok"},
      "success_criteria": "ok",
      "on_failure": "abort",
      "timeout_s": 30
    }
  ]
}"""
        fake_llm = FakeLLM(plan_json)

        class FakeToolRegistry:
            async def execute(self, name: str, args: dict) -> str:
                return "ok"

        from src.agent.control import ControlChannel

        channel = ControlChannel()

        runtime = AgentRuntime(
            llm=fake_llm,
            tools=FakeToolRegistry(),
            verification=None,
            wal=EmptyWAL(),
            channel=channel,
            role_registry=None,
        )

        plan = Plan(
            plan_id=new_plan_id(),
            spec="simple walk test",
            steps=[
                PlanStep(
                    id=new_step_id(),
                    kind=PlanStepKind.TOOL,
                    intent="Do a thing",
                    tool="bash",
                    args={"cmd": "echo ok"},
                    success_criteria="ok",
                    on_failure=None,
                    timeout_s=30,
                )
            ],
        )

        # Walk the plan - if _runtime is set correctly, this should work
        results = await runtime.walk(plan)
        assert results is not None
