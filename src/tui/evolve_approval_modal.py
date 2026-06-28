"""Modal for user approval of staged prompt updates from Evolver."""

from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class EvolveApprovalModal(ModalScreen[bool]):
    """Display staged prompt changes; user approves or rejects each."""

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, staged_path: Path):
        super().__init__()
        self._staged_path = staged_path

    def compose(self) -> ComposeResult:
        if not self._staged_path.exists():
            yield Static("No staged prompt changes.")
            yield Button("OK", id="ok")
            return
        data = json.loads(self._staged_path.read_text())
        yield Vertical(
            Static(f"## {len(data['changes'])} staged prompt update(s)"),
            *[self._render_change(name, change, rationale)
              for name, change, rationale in zip(
                  data["changes"].keys(),
                  data["changes"].values(),
                  [data["rationale"].get(n, "") for n in data["changes"].keys()],
              )],
            Horizontal(
                Button("Approve All", id="approve_all", variant="success"),
                Button("Reject All", id="reject_all", variant="error"),
                Button("Cancel", id="cancel"),
            ),
        )

    def _render_change(self, name: str, change: dict, rationale: str) -> Static:
        return Static(
            f"[bold]{name} v{change['version']}[/bold]\n"
            f"  {rationale}\n"
            f"  Prompt preview: {change['system_prompt'][:120]}..."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve_all":
            self.dismiss(True)
        elif event.button.id == "reject_all":
            self.dismiss(False)
        else:
            self.dismiss(None)
