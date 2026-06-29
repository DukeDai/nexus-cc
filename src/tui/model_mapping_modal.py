"""Modal for inspecting and remapping ModelHint → model name mappings (v1.2).

Reads the active ModelPolicy, shows one row per hint with its resolved
model, and lets the user override defaults inline. On Save, the modal
writes the new `defaults:` section to `.nexus/policy.yaml` and dismisses
with the updated ModelPolicy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ..llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy


class ModelMappingModal(ModalScreen[ModelPolicy | None]):
    """Display + edit hint-to-model mappings.

    Bindings:
        s       — Save and dismiss with updated policy
        escape  — Dismiss without saving
    """

    BINDINGS = [
        Binding("s", "save", "Save", show=True),
        Binding("escape", "dismiss_none", "Cancel", show=True),
    ]

    def __init__(self, *, policy: ModelPolicy, project_root: Path) -> None:
        super().__init__()
        self._policy = policy
        self._project_root = project_root
        # Working copy of defaults — mutated in-memory before Save.
        self._working_defaults: dict[ModelHint, str] = {
            hint: policy.defaults.get(hint, DEFAULT_POLICY.get(hint, ""))
            for hint in ModelHint
        }
        # Placeholder for Input widgets — populated in compose() once the
        # Textual app context is active. Tests can still verify intent by
        # checking _working_defaults has one entry per hint.
        self._inputs: dict[str, Input] = {}

    # --------------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        rows: list[Any] = [Label("[bold]Hint → Model mappings[/bold]")]
        for hint in ModelHint:
            current = self._working_defaults[hint]
            # Label shows the hint name + the resolved model in parentheses.
            rows.append(Label(f"{hint.value}:"))
            inp = Input(value=current, id=f"input-{hint.value}", placeholder=current)
            self._inputs[hint.value] = inp
            rows.append(inp)
        rows.append(Label("[dim]Press 's' to save, 'esc' to cancel.[/dim]"))
        rows.append(Static("", id="status-line"))
        yield Vertical(*rows)

    # ------------------------------------------------------------------ save

    def action_save(self) -> None:
        """Read inputs, build a new ModelPolicy, persist, dismiss."""
        # Capture any in-flight edit on the currently focused widget.
        self._capture_focused()
        new_defaults: dict[ModelHint, str] = {}
        for hint in ModelHint:
            raw = self._working_defaults[hint].strip()
            if not raw:
                # Empty input — keep the prior resolved model (don't blank it).
                raw = self._policy.defaults.get(hint, DEFAULT_POLICY.get(hint, ""))
            new_defaults[hint] = raw
        # Build the updated policy. Preserve cli_override / per_role / env.
        new_policy = ModelPolicy(
            cli_override=self._policy.cli_override,
            per_role=dict(self._policy.per_role),
            defaults=new_defaults,
            env_overrides=dict(self._policy.env_overrides),
        )
        # Persist to .nexus/policy.yaml — overwrite the defaults section
        # but leave other sections intact. We use a simple textual round-trip:
        # if the file exists we replace the "defaults:" block; otherwise we
        # create a starter file with create_default_yaml + then overwrite
        # the defaults.
        try:
            self._write_policy_yaml(new_defaults)
        except Exception as exc:  # pragma: no cover — disk errors are rare
            self._set_status(f"[red]Save failed: {exc}[/red]")
            return
        self.dismiss(new_policy)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    # -------------------------------------------------------------- helpers

    def _capture_focused(self) -> None:
        """Snapshot the currently focused Input into working_defaults."""
        focused = self.focused
        if isinstance(focused, Input) and focused.id:
            hint_name = focused.id.removeprefix("input-")
            try:
                hint = ModelHint(hint_name)
            except ValueError:
                return
            self._working_defaults[hint] = focused.value

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status-line", Static).update(text)
        except Exception:
            pass

    def _write_policy_yaml(self, new_defaults: dict[ModelHint, str]) -> None:
        """Write the new defaults section to .nexus/policy.yaml.

        Strategy: read the existing file, find a `defaults:` block and
        replace it; if no `defaults:` block exists, append one. Other
        sections (per_role, etc.) are preserved verbatim.
        """
        import yaml  # local import — yaml is a soft dep at runtime

        policy_path = self._project_root / ".nexus" / "policy.yaml"
        existing: dict[str, Any] = {}
        if policy_path.exists():
            try:
                with policy_path.open("r") as f:
                    loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                # Malformed YAML — overwrite from scratch.
                existing = {}
        # Replace the defaults section.
        existing["defaults"] = {h.value: m for h, m in new_defaults.items()}
        # Ensure parent dir exists.
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        with policy_path.open("w") as f:
            yaml.safe_dump(existing, f, sort_keys=False)
