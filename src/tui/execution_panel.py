"""ExecutionPanel - Textual Container with a RichLog showing walker events."""
from __future__ import annotations

from textual.containers import Container
from textual.widgets import RichLog

from ..agent.control import ControlChannel
from ..agent.events import (
    Aborted,
    AskUser,
    Paused,
    PlanCompleted,
    PlanStarted,
    Resumed,
    StepCompleted,
    StepFailed,
    StepStarted,
    ToolCallCompleted,
    ToolCallStarted,
    WalkEvent,
)


class ExecutionPanel(Container):
    """Right-top pane: streams walker events into a RichLog.

    Drains ControlChannel._events on a 0.1s interval and writes a
    colorized summary of each event into the RichLog.
    """

    DEFAULT_CSS = """
    ExecutionPanel {
        height: 100%;
    }
    ExecutionPanel RichLog {
        height: 100%;
    }
    """

    def __init__(self, *, channel: ControlChannel, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channel = channel
        self.exec_log: RichLog = RichLog(
            id="exec-log", highlight=True, markup=True, wrap=True
        )

    def compose(self):
        yield self.exec_log

    def on_mount(self) -> None:
        # Drain walker events ~10x/sec to keep the log responsive.
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
        """Dispatch a single WalkEvent to the right RichLog line."""
        if isinstance(event, PlanStarted):
            self.exec_log.write(
                f"[bold cyan]Plan started:[/bold cyan] {event.plan.spec} "
                f"({len(event.plan.steps)} steps)"
            )
        elif isinstance(event, StepStarted):
            self.exec_log.write(
                f"[cyan]Step {event.index + 1}/{event.total}: {event.step.intent}[/cyan]"
            )
        elif isinstance(event, ToolCallStarted):
            self.exec_log.write(
                f"[yellow]→ {event.tool}({event.args})[/yellow]"
            )
        elif isinstance(event, ToolCallCompleted):
            self.exec_log.write("[green]✓ tool done[/green]")
        elif isinstance(event, StepCompleted):
            self.exec_log.write("[green]✓ step complete[/green]")
        elif isinstance(event, StepFailed):
            self.exec_log.write(f"[red]✗ step failed: {event.error}[/red]")
        elif isinstance(event, AskUser):
            self.exec_log.write(f"[magenta]? {event.question}[/magenta]")
        elif isinstance(event, Paused):
            self.exec_log.write(
                f"[yellow]⏸ paused at {event.step_id or 'start'}[/yellow]"
            )
        elif isinstance(event, Resumed):
            self.exec_log.write("[green]▶ resumed[/green]")
        elif isinstance(event, Aborted):
            self.exec_log.write(f"[bold red]✗ aborted: {event.reason}[/bold red]")
        elif isinstance(event, PlanCompleted):
            self.exec_log.write(
                f"[bold green]Plan complete ({len(event.results)} steps)[/bold green]"
            )
        else:
            self.exec_log.write(f"[dim]{event}[/dim]")