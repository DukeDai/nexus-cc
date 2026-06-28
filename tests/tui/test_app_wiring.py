"""Tests for new panel + modal wiring in NexusApp."""
from __future__ import annotations

import pytest
from src.agent.control import ControlChannel
from src.tui.app import NexusApp
from src.tui.verifier_panel import VerifierPanel
from src.tui.memory_panel import MemoryPanel


@pytest.mark.asyncio
async def test_app_has_verifier_and_memory_panels():
    """Verify NexusApp mounts the new panels (smoke test)."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    async with app.run_test() as pilot:
        await pilot.pause()
        try:
            app.query_one(VerifierPanel)
            app.query_one(MemoryPanel)
        except Exception:
            pytest.fail("VerifierPanel or MemoryPanel not mounted in NexusApp")