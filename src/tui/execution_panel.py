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

    Subscribes to WalkEvents through NexusApp's single dispatcher
    (no per-panel set_interval — see NexusApp docstring for race details).
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
        # Subscribe for every WalkEvent type — the dispatcher fans events
        # out to subscribers. We subscribe per concrete type so we don't
        # depend on MRO iteration in the dispatcher.
        for ev_type in (
            PlanStarted,
            StepStarted,
            ToolCallStarted,
            ToolCallCompleted,
            StepCompleted,
            StepFailed,
            AskUser,
            Paused,
            Resumed,
            Aborted,
            PlanCompleted,
        ):
            self.app.subscribe_event(ev_type, self._handle_event)

    # ------------------------------------------------------------------ events

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
