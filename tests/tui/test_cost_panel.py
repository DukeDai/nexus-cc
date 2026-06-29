"""Tests for CostPanel (v1.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.control import ControlChannel
from src.agent.events import PlanStarted, StepCompleted
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind
from src.llm.cost_tracker import CostRecord, CostTracker
from src.llm.model_policy import ModelHint
from src.tui.app import NexusApp
from src.tui.cost_panel import ALL_HINTS, CostPanel


def _make_plan(*intents: str) -> Plan:
    steps = [
        PlanStep(
            id=f"s{i}",
            kind=PlanStepKind.TOOL,
            intent=intent,
            tool="Bash",
            args={},
            success_criteria="ok",
            on_failure=OnFailure.ASK,
            timeout_s=60,
        )
        for i, intent in enumerate(intents)
    ]
    return Plan(plan_id="p1", spec="spec", steps=steps)


def _record(model: str, hint: ModelHint, prompt: int = 100, completion: int = 50) -> CostRecord:
    from src.llm.cost_tracker import estimate_cost
    return CostRecord(
        model=model,
        hint=hint,
        role=None,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=estimate_cost(model, prompt, completion),
        timestamp=0.0,
    )


def test_cost_panel_empty_when_no_tracker():
    panel = CostPanel(cost_tracker=None)
    out = panel.render()
    assert "disabled" in str(out).lower()


def test_cost_panel_empty_when_no_records(tmp_path: Path):
    tracker = CostTracker(project_root=tmp_path, wal=None, buffer_size=100)
    panel = CostPanel(cost_tracker=tracker)
    panel.update_costs()
    out = panel.render()
    assert "no llm calls" in str(out).lower()


def test_cost_panel_shows_total_and_breakdown(tmp_path: Path):
    tracker = CostTracker(project_root=tmp_path, wal=None, buffer_size=100)
    tracker.emit(_record("claude-sonnet-4-6", ModelHint.PLANNER, prompt=1000, completion=500))
    tracker.emit(_record("claude-haiku-4-5", ModelHint.VERIFIER_SECURITY, prompt=2000, completion=200))

    panel = CostPanel(cost_tracker=tracker)
    panel.update_costs()
    out = str(panel.render())
    # Should mention both models and the hint breakdown.
    assert "claude-sonnet-4-6" in out
    assert "claude-haiku-4-5" in out
    assert "verifier_security" in out
    assert "planner" in out
    # Total: sonnet (3.00*1 + 15*0.5 = 3 + 7.5 = 10.5) + haiku (0.8*2 + 4*0.2 = 1.6+0.8 = 2.4)
    # Total = $0.0129
    assert "$0.0129" in out


def test_cost_panel_handles_malformed_buffer_gracefully(tmp_path: Path):
    """Tracker methods raising must not crash the panel."""
    tracker = CostTracker(project_root=tmp_path, wal=None, buffer_size=100)

    class BoomTracker:
        def aggregate_by(self, dim):
            raise RuntimeError("nope")

    panel = CostPanel(cost_tracker=BoomTracker())  # type: ignore[arg-type]
    panel.update_costs()  # should not raise
    out = panel.render()
    assert "no llm calls" in str(out).lower()


def test_all_hints_exposes_enum_values():
    # Sanity: every ModelHint member is listed.
    assert set(ALL_HINTS) == set(ModelHint)


@pytest.mark.asyncio
async def test_cost_panel_refreshes_on_step_completed(tmp_path: Path):
    """App should refresh the CostPanel after each StepCompleted event."""
    channel = ControlChannel()
    tracker = CostTracker(project_root=tmp_path, wal=None, buffer_size=100)
    tracker.emit(_record("claude-sonnet-4-6", ModelHint.PLANNER))
    app = NexusApp(channel=channel, cost_tracker=tracker)

    plan = _make_plan("a")
    async with app.run_test() as pilot:
        await channel.emit(PlanStarted(plan=plan))
        await channel.emit(StepCompleted(step=plan.steps[0], result="ok"))
        for _ in range(40):
            await pilot.pause()
        panel = app.query_one("#cost-panel", CostPanel)
        out = str(panel.render())
        assert "claude-sonnet-4-6" in out
        await pilot.press("ctrl+c")
