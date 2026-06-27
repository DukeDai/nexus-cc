"""NewTaskModal - Textual ModalScreen for capturing a new task description.

Opens via the 'n' binding on NexusApp. On submit (Enter), calls the
``on_submit`` callback with the typed string and dismisses.
"""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class NewTaskModal(ModalScreen[str | None]):
    """Modal form for entering a new task.

    On submit (Enter pressed or Save button clicked), invokes
    ``on_submit(task)`` with the trimmed task string and dismisses
    with the same value as the dismiss result.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    NewTaskModal {
        align: center middle;
    }
    NewTaskModal > Grid {
        grid-size: 2;
        grid-gutter: 1;
        padding: 1;
        border: thick $primary;
        width: 80;
        height: auto;
    }
    NewTaskModal Button {
        margin: 1;
    }
    """

    def __init__(self, *, on_submit: Callable[[str], None]) -> None:
        super().__init__()
        self._on_submit = on_submit

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label("Task")
            yield Input(placeholder="describe the task...", id="task-input")
            yield Button("Cancel", id="cancel-button")
            yield Button("Start", id="start-button", variant="primary")

    def on_mount(self) -> None:
        # Focus the input after the next refresh so compose() has had a
        # chance to attach the children (on_mount can fire before that).
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        try:
            self.query_one("#task-input", Input).focus()
        except Exception:
            # Compose not finished yet — try again next refresh.
            self.call_after_refresh(self._focus_input)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter pressed in the Input → start the task.
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-button":
            self._submit()
        elif event.button.id == "cancel-button":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        value = self.query_one("#task-input", Input).value.strip()
        if not value:
            self.app.notify("Task cannot be empty", severity="warning")
            return
        self._on_submit(value)
        self.dismiss(value)
