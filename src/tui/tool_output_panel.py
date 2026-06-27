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

    Drains ControlChannel._events on a 0.1s interval and updates the
    Static child in response to ToolCallStarted / ToolCallCompleted.
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
        # Drain walker events ~10x/sec to keep the panel responsive.
        self.set_interval(0.1, self._drain_events)

    # ------------------------------------------------------------------ events

    def _drain_events(self) -> None:
        """Pull all currently-queued events from the channel and handle them."""
        while True:
            event = self.channel.try_recv_event()
            if event is None:
                return
            self._handle_event(event)

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
