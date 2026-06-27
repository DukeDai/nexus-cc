"""Tests for PlanPanel - Textual Container with Tree widget + key bindings."""
from __future__ import annotations

import pytest

from src.agent.control import ControlChannel
from src.agent.events import (
    PlanStarted,
    StepCompleted,
    StepFailed,
    StepStarted,
)
from src.agent.plan import (
    OnFailure,
    Plan,
    PlanStep,
    PlanStepKind,
)
from src.tui.app import NexusApp


def _make_plan(*intents: str) -> Plan:
    """Helper: build a Plan with one step per intent string."""
    steps = [
        PlanStep(
            id=f"step_{i}",
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
    return Plan(plan_id="plan_test", spec="test spec", steps=steps)


@pytest.mark.asyncio
async def test_panel_renders_steps_as_tree_nodes():
    """PlanPanel mounts a Tree and adds a leaf per step on PlanStarted."""
    channel = ControlChannel()
    plan = _make_plan("first step", "second step", "third step")
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        # Find the PlanPanel widget
        plan_panel = app.query_one("#plan-pane")
        # Drain should pull the event we put in below
        await channel.emit(PlanStarted(plan=plan))
        # Give the interval timer time to fire
        for _ in range(20):
            await pilot.pause()
            if len(plan_panel.tree.root.children) == 3:
                break
        assert len(plan_panel.tree.root.children) == 3
        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_panel_marks_completed_steps():
    """StepCompleted updates the matching tree node label to include ✓."""
    channel = ControlChannel()
    plan = _make_plan("do thing one", "do thing two")
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        plan_panel = app.query_one("#plan-pane")
        await channel.emit(PlanStarted(plan=plan))
        # Wait for both nodes to be rendered
        for _ in range(20):
            await pilot.pause()
            if len(plan_panel.tree.root.children) == 2:
                break
        step0 = plan.steps[0]
        await channel.emit(StepStarted(step=step0, index=0, total=2))
        await channel.emit(StepCompleted(step=step0, result="ok"))
        # Wait for label update
        for _ in range(20):
            await pilot.pause()
            labels = [str(c.label) for c in plan_panel.tree.root.children]
            if any("✓" in lab for lab in labels):
                break
        labels = [str(c.label) for c in plan_panel.tree.root.children]
        assert any("✓" in lab for lab in labels), labels
        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_panel_marks_failed_steps():
    """StepFailed updates the matching tree node label to include ✗."""
    channel = ControlChannel()
    plan = _make_plan("do thing one")
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        plan_panel = app.query_one("#plan-pane")
        await channel.emit(PlanStarted(plan=plan))
        for _ in range(20):
            await pilot.pause()
            if len(plan_panel.tree.root.children) == 1:
                break
        step0 = plan.steps[0]
        await channel.emit(StepFailed(step=step0, error="boom"))
        for _ in range(20):
            await pilot.pause()
            labels = [str(c.label) for c in plan_panel.tree.root.children]
            if any("✗" in lab for lab in labels):
                break
        labels = [str(c.label) for c in plan_panel.tree.root.children]
        assert any("✗" in lab for lab in labels), labels
        await pilot.press("ctrl+c")
