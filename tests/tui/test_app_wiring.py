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


@pytest.mark.asyncio
async def test_v_keybinding_focuses_verifier_panel():
    from src.tui.app import NexusApp
    from src.tui.verifier_panel import VerifierPanel
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    app.workdir = "."
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("V")
        await pilot.pause()
        focused = app.focused
        assert isinstance(focused, VerifierPanel)


@pytest.mark.asyncio
async def test_m_keybinding_focuses_memory_panel():
    from src.tui.app import NexusApp
    from src.tui.memory_panel import MemoryPanel
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    app.workdir = "."
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("M")
        await pilot.pause()
        focused = app.focused
        assert isinstance(focused, MemoryPanel)


@pytest.mark.asyncio
async def test_s_keybinding_pushes_skill_picker():
    from src.tui.app import NexusApp
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    app.workdir = "."
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert app.screen_stack[-1].__class__.__name__ == "SkillPickerModal"


@pytest.mark.asyncio
async def test_e_keybinding_pushes_evolve_approval():
    from src.tui.app import NexusApp
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    app.workdir = "."
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("E")
        await pilot.pause()
        assert app.screen_stack[-1].__class__.__name__ == "EvolveApprovalModal"