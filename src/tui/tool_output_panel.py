"""ToolOutputPanel - Textual Container showing the most recent tool I/O."""
from __future__ import annotations

from textual.containers import Container
from textual.widgets import Static

from ..agent.control import ControlChannel
from ..agent.events import (
    ToolCallCompleted,
    ToolCallStarted,
    WalkEvent,
)


class ToolOutputPanel(Container):
    """Right-bottom pane: renders the last tool call's I/O.

    Subscribes to WalkEvents through NexusApp's single dispatcher
    (no per-panel set_interval — see NexusApp docstring for race details).
    """

    DEFAULT_CSS = """
    ToolOutputPanel {
        height: 100%;
    }
    ToolOutputPanel Static {
        height: 100%;
    }
    """

    def __init__(self, *, channel: ControlChannel, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channel = channel

    def compose(self):
        yield Static("(no tool calls yet)", id="tool-output")

    def on_mount(self) -> None:
        self.app.subscribe_event(ToolCallStarted, self._handle_event)
        self.app.subscribe_event(ToolCallCompleted, self._handle_event)

    # ------------------------------------------------------------------ events

    def _handle_event(self, event: WalkEvent) -> None:
        """Dispatch a single WalkEvent to the right Static update."""
        try:
            static = self.query_one("#tool-output", Static)
        except Exception:
            return
        if isinstance(event, ToolCallStarted):
            static.update(
                f"[yellow]→ {event.tool}[/yellow]\n"
                f"args: {event.args}\n"
                f"step: {event.step_id}"
            )
        elif isinstance(event, ToolCallCompleted):
            static.update(
                f"[green]✓ tool done[/green]\n"
                f"result: {event.result}\n"
                f"step: {event.step_id}"
            )
        # Other events are acknowledged but not rendered in this panel.
