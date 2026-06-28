from unittest.mock import MagicMock
from src.agent.evolution import Evolver, StagedChanges
from src.agent.plan import Plan, PlanStep, PlanStepKind


def _make_results(*statuses):
    return [
        MagicMock(status=status, error_category=("io_error" if status == "failed" else None))
        for status in statuses
    ]


def test_record_outcome_computes_error_histogram():
    wal = MagicMock()
    memory = MagicMock()
    feedback = MagicMock()
    evolver = Evolver(wal=wal, memory=memory, feedback=feedback)
    plan = Plan(plan_id="p1", spec="test plan", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, intent="Read a file", tool="Read"),
        PlanStep(id="s2", kind=PlanStepKind.TOOL, intent="Write a file", tool="Write"),
    ])
    results = _make_results("completed", "failed", "failed")
    evolver.record_outcome(plan, results)
    assert evolver._last_outcome["total_steps"] == 2
    assert evolver._last_outcome["failed_count"] == 2
    assert "io_error" in evolver._last_outcome["error_histogram"]


def test_staged_changes_default_empty():
    sc = StagedChanges(changes={}, rationale={})
    assert sc.changes == {}
    assert sc.rationale == {}