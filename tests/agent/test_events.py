"""Tests for WalkEvent hierarchy."""
from __future__ import annotations

from datetime import datetime

from agent.plan import Plan, PlanStep, PlanStepKind, OnFailure
from agent.events import (
    WalkEvent,
    PlanStarted,
    StepStarted,
    ToolCallStarted,
    ToolCallCompleted,
    StepCompleted,
    StepFailed,
    AskUser,
    Paused,
    Resumed,
    Aborted,
    PlanCompleted,
)


def _make_plan() -> Plan:
    """Helper to create a minimal Plan for testing."""
    step = PlanStep(
        id="step_abc123",
        kind=PlanStepKind.TOOL,
        intent="Read the config file",
        tool="Read",
        args={"path": "config.py"},
    )
    return Plan(
        plan_id="plan_test123",
        spec="Test plan",
        steps=[step],
        assumptions=[],
        risks=[],
        created_at=datetime.now(),
    )


def _make_step() -> PlanStep:
    """Helper to create a minimal PlanStep for testing."""
    return PlanStep(
        id="step_xyz789",
        kind=PlanStepKind.TOOL,
        intent="Edit the config",
        tool="Edit",
        args={"path": "config.py"},
    )


class TestWalkEventSubclasses:
    """Test that all 11 subclasses are instances of WalkEvent."""

    def test_plan_started_is_walk_event(self):
        plan = _make_plan()
        e = PlanStarted(plan=plan)
        assert isinstance(e, WalkEvent)

    def test_step_started_is_walk_event(self):
        s = _make_step()
        e = StepStarted(step=s, index=2, total=5)
        assert isinstance(e, WalkEvent)

    def test_tool_call_started_is_walk_event(self):
        e = ToolCallStarted(tool="Read", args={"path": "config.py"}, step_id="step_abc")
        assert isinstance(e, WalkEvent)

    def test_tool_call_completed_is_walk_event(self):
        e = ToolCallCompleted(result={"output": "ok"}, step_id="step_abc")
        assert isinstance(e, WalkEvent)

    def test_step_completed_is_walk_event(self):
        s = _make_step()
        e = StepCompleted(step=s, result="passed")
        assert isinstance(e, WalkEvent)

    def test_step_failed_is_walk_event(self):
        s = _make_step()
        e = StepFailed(step=s, error="timeout")
        assert isinstance(e, WalkEvent)

    def test_ask_user_is_walk_event(self):
        s = _make_step()
        e = AskUser(step=s, question="Which option?", options=["A", "B"])
        assert isinstance(e, WalkEvent)

    def test_paused_is_walk_event(self):
        e = Paused(step_id="step_abc")
        assert isinstance(e, WalkEvent)

    def test_paused_none_is_walk_event(self):
        e = Paused(step_id=None)
        assert isinstance(e, WalkEvent)

    def test_resumed_is_walk_event(self):
        e = Resumed()
        assert isinstance(e, WalkEvent)

    def test_aborted_is_walk_event(self):
        e = Aborted(reason="user cancelled")
        assert isinstance(e, WalkEvent)

    def test_plan_completed_is_walk_event(self):
        e = PlanCompleted(results=["step1_ok", "step2_ok"])
        assert isinstance(e, WalkEvent)


class TestPlanStartedPayload:
    """Test that PlanStarted carries the plan field correctly."""

    def test_plan_started_carries_plan(self):
        plan = _make_plan()
        e = PlanStarted(plan=plan)
        assert e.plan is plan
        assert e.plan.plan_id == "plan_test123"
        assert e.plan.spec == "Test plan"


class TestStepStartedPayload:
    """Test that StepStarted carries step/index/total correctly."""

    def test_step_started_carries_fields(self):
        step = _make_step()
        e = StepStarted(step=step, index=2, total=5)
        assert e.step is step
        assert e.index == 2
        assert e.total == 5
        assert e.step.intent == "Edit the config"
