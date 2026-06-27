"""Tests for Plan and PlanStep data model."""
from datetime import datetime

import pytest

from agent.plan import (
    OnFailure,
    Plan,
    PlanStep,
    PlanStepKind,
    new_plan_id,
    new_step_id,
)


class TestRoundTrip:
    def test_to_dict_from_dict_preserves_all_fields(self):
        """Round-trip via to_dict/from_dict preserves plan_id, spec, steps, assumptions, risks, created_at, version."""
        plan = Plan(
            plan_id="plan_abc12345",
            spec="Test plan",
            steps=[
                PlanStep(
                    id="step_001",
                    kind=PlanStepKind.TOOL,
                    intent="Run tests",
                    tool="bash",
                    args={"cmd": "pytest"},
                    success_criteria="All tests pass",
                    on_failure=OnFailure.RETRY,
                    timeout_s=60,
                ),
                PlanStep(
                    id="step_002",
                    kind=PlanStepKind.VERIFY,
                    intent="Verify output",
                    tool=None,
                    args={},
                    success_criteria="Output non-empty",
                    on_failure=OnFailure.ASK,
                    timeout_s=30,
                ),
            ],
            assumptions=["System is online"],
            risks=["Network may fail"],
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            version=3,
        )

        d = plan.to_dict()
        restored = Plan.from_dict(d)

        assert restored.plan_id == plan.plan_id
        assert restored.spec == plan.spec
        assert len(restored.steps) == 2
        assert restored.steps[0].id == "step_001"
        assert restored.steps[0].kind == PlanStepKind.TOOL
        assert restored.steps[0].tool == "bash"
        assert restored.steps[0].args == {"cmd": "pytest"}
        assert restored.steps[0].success_criteria == "All tests pass"
        assert restored.steps[0].on_failure == OnFailure.RETRY
        assert restored.steps[0].timeout_s == 60
        assert restored.steps[1].id == "step_002"
        assert restored.steps[1].kind == PlanStepKind.VERIFY
        assert restored.assumptions == ["System is online"]
        assert restored.risks == ["Network may fail"]
        assert restored.created_at == plan.created_at
        assert restored.version == 3

    def test_created_at_serialized_as_iso_string(self):
        """created_at is serialized as ISO format string in to_dict."""
        plan = Plan(
            plan_id="plan_xyz",
            spec="Test",
            created_at=datetime(2024, 6, 1, 14, 22, 0),
        )
        d = plan.to_dict()
        assert d["created_at"] == "2024-06-01T14:22:00"
        assert isinstance(d["created_at"], str)


class TestFindStep:
    def test_find_step_returns_matching_step(self):
        """find_step(step_id) returns the PlanStep with that id."""
        plan = Plan(
            plan_id="plan_test",
            spec="Test",
            steps=[
                PlanStep(id="step_a", kind=PlanStepKind.TOOL, intent="Do thing"),
                PlanStep(id="step_b", kind=PlanStepKind.VERIFY, intent="Check thing"),
                PlanStep(id="step_c", kind=PlanStepKind.CRITIQUE, intent="Review thing"),
            ],
        )
        found = plan.find_step("step_b")
        assert found is not None
        assert found.id == "step_b"
        assert found.kind == PlanStepKind.VERIFY

    def test_find_step_returns_none_for_missing_id(self):
        """find_step returns None for an id not in steps."""
        plan = Plan(
            plan_id="plan_test",
            spec="Test",
            steps=[
                PlanStep(id="step_x", kind=PlanStepKind.TOOL, intent="Do thing"),
            ],
        )
        found = plan.find_step("step_nonexistent")
        assert found is None


class TestVersionSemantics:
    def test_plan_version_defaults_to_one(self):
        """Plan.version defaults to 1 when not specified."""
        plan = Plan(plan_id="plan_v", spec="Test")
        assert plan.version == 1

    def test_plan_version_can_be_incremented(self):
        """Plan.version can be set and modified (edits bump version)."""
        plan = Plan(plan_id="plan_v", spec="Test", version=1)
        plan.version = 2
        assert plan.version == 2
        plan.version = 5
        assert plan.version == 5