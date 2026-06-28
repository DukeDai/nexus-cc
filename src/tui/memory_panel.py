"""TUI panel showing memory index stats."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


class MemoryPanel(Static):
    """Displays memory index stats (episodic + semantic + skill counts)."""

    stats: reactive[dict] = reactive({})

    def render(self):
        if not self.stats:
            return Text("Memory not warmed.", style="dim")
        epi = self.stats.get("episodic_count", 0)
        sem = self.stats.get("semantic_count", 0)
        skills = self.stats.get("skill_count", 0)
        return Text(
            f"Episodic: {epi} plans\n"
            f"Semantic: {sem} chunks\n"
            f"Skills: {skills} loaded"
        )

    def update_stats(self, stats: dict) -> None:
        self.stats = stats