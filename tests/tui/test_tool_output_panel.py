"""Tests for ToolOutputPanel - Textual Container with Static widget showing
the most recent tool call's I/O.

These tests mount the panel in a minimal app (without the other draining
panels) to avoid the queue-drain race that exists between sibling panels
sharing one ControlChannel.
"""
from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Static

from src.agent.control import ControlChannel
from src.agent.events import ToolCallCompleted, ToolCallStarted
from src.tui.tool_output_panel import ToolOutputPanel


class _ToolOutputApp(App):
    """Minimal host that mounts ONLY the ToolOutputPanel.

    The full NexusApp also mounts PlanPanel + ExecutionPanel, each of which
    drains the same ControlChannel queue. To make this test deterministic
    (and independent of the sibling panels' drain schedule), we mount only
    the panel under test.
    """

    def __init__(self, channel: ControlChannel) -> None:
        super().__init__()
        self.channel = channel

    def compose(self):
        yield ToolOutputPanel(channel=self.channel, id="tool-output-pane")


@pytest.mark.asyncio
async def test_panel_shows_tool_started_info():
    """ToolOutputPanel shows the most recent ToolCallStarted details."""
    channel = ControlChannel()
    app = _ToolOutputApp(channel=channel)

    async with app.run_test() as pilot:
        # Drive a ToolCallStarted event into the channel
        await channel.emit(
            ToolCallStarted(tool="Read", args={"path": "/tmp/foo.txt"}, step_id="step_0")
        )

        # Wait for the interval timer to drain and update Static
        static_widget = None
        for _ in range(30):
            await pilot.pause(0.05)
            try:
                static_widget = app.query_one("#tool-output", Static)
            except Exception:
                continue
            content = str(static_widget.render())
            if "Read" in content and "/tmp/foo.txt" in content:
                break

        assert static_widget is not None, "#tool-output Static not found"
        content = str(static_widget.render())
        assert "Read" in content
        assert "/tmp/foo.txt" in content
        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_panel_updates_on_tool_completed():
    """ToolOutputPanel shows completion info after ToolCallCompleted."""
    channel = ControlChannel()
    app = _ToolOutputApp(channel=channel)

    async with app.run_test() as pilot:
        await channel.emit(
            ToolCallStarted(tool="Bash", args={"cmd": "ls"}, step_id="step_1")
        )

        # Wait for started info to render
        static_widget = None
        for _ in range(30):
            await pilot.pause(0.05)
            try:
                static_widget = app.query_one("#tool-output", Static)
            except Exception:
                continue
            content = str(static_widget.render())
            if "Bash" in content and "ls" in content:
                break

        assert static_widget is not None
        content = str(static_widget.render())
        assert "Bash" in content
        assert "ls" in content

        # Now drive a completion event
        await channel.emit(
            ToolCallCompleted(result="file1.txt\nfile2.txt", step_id="step_1")
        )

        for _ in range(30):
            await pilot.pause(0.05)
            content = str(static_widget.render())
            if "tool done" in content and "file1.txt" in content:
                break

        content = str(static_widget.render())
        assert "tool done" in content
        assert "file1.txt" in content
        await pilot.press("ctrl+c")
