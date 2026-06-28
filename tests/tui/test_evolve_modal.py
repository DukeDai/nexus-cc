import pytest
from pathlib import Path
from textual.app import App
from src.tui.evolve_approval_modal import EvolveApprovalModal


@pytest.mark.asyncio
async def test_modal_renders_no_staged_message(tmp_path):
    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(EvolveApprovalModal(tmp_path / "staged.json"))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Modal mounted with no-staged message.
