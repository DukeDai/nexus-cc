"""RecoverModal - shown at startup if an unfinished plan exists in the WAL."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class RecoverModal(ModalScreen[bool]):
    """Prompts user to Resume or Discard a recovered plan.

    Dismisses with True if user wants to resume, False to discard.
    """

    BINDINGS = [
        Binding("y", "resume", "Resume"),
        Binding("n", "discard", "Discard"),
        Binding("escape", "discard", "Discard"),
    ]

    def __init__(self, *, plan_id: str, completed: int, total: int) -> None:
        super().__init__()
        self._plan_id = plan_id
        self._completed = completed
        self._total = total

    def compose(self) -> ComposeResult:
        with Grid(id="recover-grid"):
            yield Static("[bold]Unfinished plan found[/bold]", id="recover-title")
            yield Static(f"Plan: {self._plan_id}", id="recover-plan-id")
            yield Static(
                f"Progress: {self._completed}/{self._total} steps complete",
                id="recover-progress",
            )
            with Horizontal(id="recover-buttons"):
                yield Button("Resume (y)", id="resume-btn", variant="primary")
                yield Button("Discard (n)", id="discard-btn", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume-btn":
            self.dismiss(True)
        elif event.button.id == "discard-btn":
            self.dismiss(False)

    def action_resume(self) -> None:
        self.dismiss(True)

    def action_discard(self) -> None:
        self.dismiss(False)