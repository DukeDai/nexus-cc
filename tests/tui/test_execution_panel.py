"""Tests for ExecutionPanel - Textual Container with RichLog of walker events."""
from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App
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
from src.tui.execution_panel import ExecutionPanel


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


class _PanelHarness(App):
    """Minimal harness mounting only the ExecutionPanel under test.

    Avoids the PlanPanel-vs-ExecutionPanel race for events on the shared
    channel queue by hosting the panel in isolation.
    """

    def __init__(self, channel: ControlChannel) -> None:
        super().__init__()
        self.channel = channel

    def compose(self):
        yield ExecutionPanel(channel=self.channel, id="execution-pane")


@pytest.mark.asyncio
async def test_panel_logs_plan_started():
    """ExecutionPanel writes a 'Plan started' line to its RichLog on PlanStarted."""
    channel = ControlChannel()
    plan = _make_plan("first step")
    app = _PanelHarness(channel)

    async with app.run_test() as pilot:
        exec_log = app.query_one("#exec-log", RichLog)
        await channel.emit(PlanStarted(plan=plan))
        # Wait for the drain interval to fire and write the line.
        for _ in range(20):
            await pilot.pause()
            if "Plan started" in _log_text(exec_log):
                break
        text = _log_text(exec_log)
        assert "Plan started" in text, text
        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_panel_logs_step_started_and_completed():
    """ExecutionPanel logs both StepStarted and StepCompleted lines for a step."""
    channel = ControlChannel()
    plan = _make_plan("do thing one", "do thing two")
    app = _PanelHarness(channel)

    async with app.run_test() as pilot:
        exec_log = app.query_one("#exec-log", RichLog)
        await channel.emit(PlanStarted(plan=plan))
        step0 = plan.steps[0]
        await channel.emit(StepStarted(step=step0, index=0, total=2))
        await channel.emit(StepCompleted(step=step0, result="ok"))
        # Wait for both lines to appear in the log.
        for _ in range(30):
            await pilot.pause()
            text = _log_text(exec_log)
            if "do thing one" in text and "step complete" in text:
                break
        text = _log_text(exec_log)
        assert "do thing one" in text, text
        assert "step complete" in text, text
        await pilot.press("ctrl+c")