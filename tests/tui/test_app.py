"""Tests for NexusApp - Textual TUI shell."""
from __future__ import annotations

import pytest
from src.agent.control import ControlChannel
from src.tui.app import NexusApp


@pytest.mark.asyncio
async def test_nexusapp_mounts_with_header_and_footer():
    """NexusApp composes Header and Footer plus the 4 named panes."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    async with app.run_test() as pilot:
        # Header and Footer are mounted by compose()
        assert app.query("Header")
        assert app.query("Footer")
        # Four panes exist as named Vertical containers
        assert app.query("#plan-pane")
        assert app.query("#execution-pane")
        assert app.query("#tool-output-pane")
        assert app.query("#right-pane")
        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_nexusapp_stores_channel_and_runtime():
    """NexusApp exposes channel/runtime attributes for later panels."""
    channel = ControlChannel()
    runtime = object()  # placeholder; type-checked by callers
    app = NexusApp(channel=channel, runtime=runtime)
    assert app.channel is channel
    assert app.runtime is runtime