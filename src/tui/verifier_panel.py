"""TUI panel showing last verifier outcome."""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


class VerifierPanel(Static):
    """Displays the last verifier pipeline outcome."""

    last_outcome: reactive[tuple[str, bool, list[str]] | None] = reactive(None)

    def render(self):
        if self.last_outcome is None:
            return Text("No verifiers run yet.", style="dim")
        name, passed, errors = self.last_outcome
        if passed:
            return Text(f"✓ {name}", style="green")
        err_preview = "\n".join(errors[:5])
        return Text(f"✗ {name}\n{err_preview}", style="red")

    def update_outcome(self, name: str, passed: bool, errors: list[str]) -> None:
        self.last_outcome = (name, passed, errors)