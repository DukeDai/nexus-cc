import pytest
from textual.app import App
from src.tui.verifier_panel import VerifierPanel
from src.tui.memory_panel import MemoryPanel


@pytest.mark.asyncio
async def test_verifier_panel_renders_pass():
    class TestApp(App):
        def compose(self):
            yield VerifierPanel(id="vp")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#vp", VerifierPanel)
        panel.update_outcome("security", True, [])
        assert panel.last_outcome == ("security", True, [])


@pytest.mark.asyncio
async def test_verifier_panel_renders_fail_with_errors():
    class TestApp(App):
        def compose(self):
            yield VerifierPanel(id="vp")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#vp", VerifierPanel)
        panel.update_outcome("test", False, ["test_foo FAILED"])
        assert panel.last_outcome[1] is False


@pytest.mark.asyncio
async def test_memory_panel_renders_stats():
    class TestApp(App):
        def compose(self):
            yield MemoryPanel(id="mp")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one("#mp", MemoryPanel)
        panel.update_stats({"episodic_count": 5, "semantic_count": 100, "skill_count": 3})
        assert panel.stats["episodic_count"] == 5