import pytest
from textual.app import App
from src.tui.skill_picker_modal import SkillPickerModal
from src.tui.prompt_history_viewer_modal import PromptHistoryViewerModal


@pytest.mark.asyncio
async def test_skill_picker_renders_empty():
    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(SkillPickerModal([]))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()


@pytest.mark.asyncio
async def test_skill_picker_renders_with_skills():
    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(SkillPickerModal([{"name": "pytest_helper"}]))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()


@pytest.mark.asyncio
async def test_prompt_history_renders():
    class TestApp(App):
        async def on_mount(self):
            from datetime import datetime
            from src.agent.prompts import PromptTemplate
            versions = [
                PromptTemplate(name="planner", system_prompt="v1", version=1,
                               updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0),
            ]
            await self.push_screen(PromptHistoryViewerModal("planner", versions))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()