"""Tests for ToolOutputPanel - Textual Container with Static widget showing
the most recent tool call's I/O.

Mounted under the production NexusApp so the subscriber-dispatcher
delivers events the same way it does at runtime (no per-panel drainer).
"""
from __future__ import annotations

import pytest
from textual.widgets import Static

from src.agent.control import ControlChannel
from src.agent.events import ToolCallCompleted, ToolCallStarted
from src.tui.app import NexusApp


@pytest.mark.asyncio
async def test_panel_shows_tool_started_info():
    """ToolOutputPanel shows the most recent ToolCallStarted details."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        await channel.emit(
            ToolCallStarted(tool="Read", args={"path": "/tmp/foo.txt"}, step_id="step_0")
        )

        # Wait for NexusApp's single dispatcher to deliver the event.
        static_widget = None
        for _ in range(40):
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
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        await channel.emit(
            ToolCallStarted(tool="Bash", args={"cmd": "ls"}, step_id="step_1")
        )

        # Wait for started info to render.
        static_widget = None
        for _ in range(40):
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

        # Now drive a completion event.
        await channel.emit(
            ToolCallCompleted(result="file1.txt\nfile2.txt", step_id="step_1")
        )

        for _ in range(40):
            await pilot.pause(0.05)
            content = str(static_widget.render())
            if "tool done" in content and "file1.txt" in content:
                break

        content = str(static_widget.render())
        assert "tool done" in content
        assert "file1.txt" in content
        await pilot.press("ctrl+c")
