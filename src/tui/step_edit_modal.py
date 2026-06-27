"""StepEditModal - Textual ModalScreen for editing a single PlanStep."""
from __future__ import annotations

import json
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, TextArea

from ..agent.plan import OnFailure, PlanStep, PlanStepKind


# Tools offered in the Select dropdown. None / blank represents "no tool"
# (e.g. for VERIFY / CRITIQUE / ASK_USER step kinds).
_TOOL_OPTIONS: list[tuple[str, str | None]] = [
    ("(none)", None),
    ("Read", "Read"),
    ("Write", "Write"),
    ("Edit", "Edit"),
    ("Bash", "Bash"),
    ("Glob", "Glob"),
    ("Grep", "Grep"),
    ("Git", "Git"),
    ("WebSearch", "WebSearch"),
]

_FAILURE_OPTIONS: list[tuple[str, OnFailure]] = [
    (of.value, of) for of in OnFailure
]


class StepEditModal(ModalScreen[PlanStep | None]):
    """Modal form to edit one PlanStep.

    On Save, validates the Args TextArea as JSON, builds a new PlanStep
    preserving ``id`` and ``kind``, invokes the ``on_save`` callback with
    it, and dismisses with the new step.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    StepEditModal {
        align: center middle;
    }
    StepEditModal > Grid {
        grid-size: 2;
        grid-gutter: 1;
        padding: 1;
        border: thick $primary;
        width: 80;
        height: auto;
    }
    StepEditModal Button {
        margin: 1;
    }
    """

    def __init__(
        self,
        *,
        step: PlanStep,
        on_save: Callable[[PlanStep], None],
    ) -> None:
        super().__init__()
        self._step = step
        self._on_save = on_save

    # --------------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label("Intent")
            yield Input(
                value=self._step.intent,
                id="intent-input",
            )

            yield Label("Tool")
            yield Select(
                options=_TOOL_OPTIONS,
                value=self._step.tool,
                allow_blank=True,
                id="tool-select",
            )

            yield Label("Args (JSON)")
            yield TextArea(
                json.dumps(self._step.args, indent=2),
                id="args-textarea",
            )

            yield Label("Success criteria")
            yield Input(
                value=self._step.success_criteria,
                id="success-input",
            )

            yield Label("On failure")
            yield Select(
                options=_FAILURE_OPTIONS,
                value=self._step.on_failure,
                allow_blank=False,
                id="failure-select",
            )

            yield Label("Timeout (s)")
            yield Input(
                value=str(self._step.timeout_s),
                type="integer",
                id="timeout-input",
            )

            yield Button("Cancel", id="cancel-button")
            yield Button("Save", id="save-button", variant="primary")

    # -------------------------------------------------------------- handlers

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-button":
            self._on_save_pressed()
        elif event.button.id == "cancel-button":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _on_save_pressed(self) -> None:
        """Validate, build a new PlanStep, fire callback, dismiss."""
        # Args: must parse as JSON.
        args_text = self.query_one("#args-textarea", TextArea).text
        try:
            new_args = json.loads(args_text) if args_text.strip() else {}
        except json.JSONDecodeError as exc:
            self.app.notify(f"Invalid JSON: {exc}", severity="error")
            return

        if not isinstance(new_args, dict):
            self.app.notify(
                "Invalid JSON: Args must be a JSON object", severity="error"
            )
            return

        # Intent
        intent = self.query_one("#intent-input", Input).value

        # Tool — Select.NULL means no selection.
        tool_select = self.query_one("#tool-select", Select)
        tool_value = tool_select.value
        if tool_value is Select.NULL or tool_value is None:
            new_tool: str | None = None
        else:
            new_tool = str(tool_value)

        # On failure
        failure_select = self.query_one("#failure-select", Select)
        failure_value = failure_select.value
        if isinstance(failure_value, OnFailure):
            new_failure: OnFailure = failure_value
        elif isinstance(failure_value, str):
            new_failure = OnFailure(failure_value)
        else:
            new_failure = OnFailure.ASK

        # Success criteria
        success = self.query_one("#success-input", Input).value

        # Timeout
        timeout_raw = self.query_one("#timeout-input", Input).value.strip()
        try:
            new_timeout = int(timeout_raw) if timeout_raw else 0
        except ValueError:
            self.app.notify("Invalid timeout: must be an integer", severity="error")
            return

        new_step = PlanStep(
            id=self._step.id,
            kind=self._step.kind,
            intent=intent,
            tool=new_tool,
            args=new_args,
            success_criteria=success,
            on_failure=new_failure,
            timeout_s=new_timeout,
        )
        self._on_save(new_step)
        self.dismiss(new_step)
