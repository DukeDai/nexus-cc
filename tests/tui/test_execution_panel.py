"""Tests for ExecutionPanel - Textual Container with RichLog of walker events."""
from __future__ import annotations

import pytest
from rich.text import Text
from textual.widgets import RichLog

from src.agent.control import ControlChannel
from src.agent.events import (
    PlanStarted,
    StepCompleted,
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


def _log_text(log: RichLog) -> str:
    """Concatenate the textual content of every RichLog line into one string."""
    parts: list[str] = []
    for line in log.lines:
        if isinstance(line, Text):
            parts.append(line.plain)
        else:
            parts.append(str(line))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_panel_logs_plan_started():
    """ExecutionPanel writes a 'Plan started' line to its RichLog on PlanStarted."""
    channel = ControlChannel()
    plan = _make_plan("first step")
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        exec_panel = app.query_one("#execution-pane")
        await channel.emit(PlanStarted(plan=plan))
        # Wait for the dispatcher (one set_interval on NexusApp) to fire
        # and deliver the event to the subscribed ExecutionPanel.
        for _ in range(40):
            await pilot.pause()
            if "Plan started" in _log_text(exec_panel.exec_log):
                break
        text = _log_text(exec_panel.exec_log)
        assert "Plan started" in text, text
        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_panel_logs_step_started_and_completed():
    """ExecutionPanel logs both StepStarted and StepCompleted lines for a step."""
    channel = ControlChannel()
    plan = _make_plan("do thing one", "do thing two")
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        exec_panel = app.query_one("#execution-pane")
        await channel.emit(PlanStarted(plan=plan))
        step0 = plan.steps[0]
        await channel.emit(StepStarted(step=step0, index=0, total=2))
        await channel.emit(StepCompleted(step=step0, result="ok"))
        # Wait for the dispatcher to fan all three events to the panel.
        for _ in range(40):
            await pilot.pause()
            text = _log_text(exec_panel.exec_log)
            if "do thing one" in text and "step complete" in text:
                break
        text = _log_text(exec_panel.exec_log)
        assert "do thing one" in text, text
        assert "step complete" in text, text
        await pilot.press("ctrl+c")
