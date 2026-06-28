"""Modal for viewing prompt template version history."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class PromptHistoryViewerModal(ModalScreen[None]):
    """Display version history of a prompt template."""

    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, name: str, versions: list):
        super().__init__()
        self._name = name
        self._versions = versions

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"# {self._name} — {len(self._versions)} version(s)"),
            *[Static(f"v{v.version} ({v.updated_at.date()}): {v.system_prompt[:100]}...")
              for v in self._versions],
        )