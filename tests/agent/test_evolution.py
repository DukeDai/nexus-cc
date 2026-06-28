from datetime import datetime
from unittest.mock import MagicMock
from src.agent.evolution import Evolver, StagedChanges
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agent.prompts import PromptTemplate, PromptTemplateRegistry


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


def test_update_prompt_registry_stages_change_when_failure_rate_high(tmp_path):
    wal = MagicMock()
    memory = MagicMock()
    memory.episodic = MagicMock(return_value=MagicMock(success_rate=MagicMock(return_value=0.2)))
    feedback = MagicMock()
    evolver = Evolver(wal=wal, memory=memory, feedback=feedback)
    plan = Plan(plan_id="p1", spec="test plan", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, intent="Read", tool="Read"),
    ])
    results = _make_results("failed", "failed", "failed")
    evolver.record_outcome(plan, results)

    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="original", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    staged = evolver.update_prompt_registry(reg)
    from src.agent.evolution import StagedChanges
    assert isinstance(staged, StagedChanges)


def test_update_prompt_registry_respects_walk_count_cap(tmp_path):
    wal = MagicMock()
    memory = MagicMock()
    memory.episodic = MagicMock(return_value=MagicMock(success_rate=MagicMock(return_value=0.1)))
    feedback = MagicMock()
    evolver = Evolver(wal=wal, memory=memory, feedback=feedback)
    plan = Plan(plan_id="p1", spec="test plan", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, intent="Read", tool="Read"),
    ])
    results = _make_results("failed")
    evolver.record_outcome(plan, results)
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v1", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    evolver.update_prompt_registry(reg)
    evolver.record_outcome(plan, results)
    staged2 = evolver.update_prompt_registry(reg)
    from src.agent.evolution import StagedChanges
    assert isinstance(staged2, StagedChanges)


def test_should_replan_returns_false_when_no_recent_failures():
    evolver = Evolver(wal=MagicMock(), memory=MagicMock(), feedback=MagicMock())
    results = _make_results("completed", "completed", "completed")
    assert evolver.should_replan(results) is False


def test_should_replan_returns_true_when_three_consecutive_failures():
    evolver = Evolver(wal=MagicMock(), memory=MagicMock(), feedback=MagicMock())
    results = _make_results("completed", "failed", "failed", "failed")
    assert evolver.should_replan(results) is True