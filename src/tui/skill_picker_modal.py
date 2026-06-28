"""Modal for attaching a skill to the focused step."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListView, ListItem


class SkillPickerModal(ModalScreen[dict | None]):
    """List of skills; user picks one to attach."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, skills: list[dict[str, Any]]):
        super().__init__()
        self._skills = skills

    def compose(self) -> ComposeResult:
        items = [
            ListItem(Label(s.get("name", "?"))) for s in self._skills
        ]
        yield Vertical(
            Label("Pick a skill to attach:"),
            ListView(*items) if items else Label("(no skills available)"),
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if 0 <= idx < len(self._skills):
            self.dismiss(self._skills[idx])
        else:
            self.dismiss(None)