"""RalphLoop State Machine Visualization.

Displays the PLAN → ACT → VERIFY → REFLECT cycle with visual indicators
for current state, transitions, retry count, and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich import box as rich_box

from src.ralphloop.states import RalphState
from src.ralphloop.orchestrator import ContextTier


# ─── Color Palette ────────────────────────────────────────────────────────────

STATE_COLORS = {
    RalphState.PLAN: "cyan",
    RalphState.ACT: "green",
    RalphState.VERIFY: "yellow",
    RalphState.REFLECT: "magenta",
    RalphState.COMMIT: "bold green",
    RalphState.ESCALATE: "bold red",
    RalphState.ABORT: "bold red",
}

STATE_DESCRIPTIONS = {
    RalphState.PLAN: "Planning: understand requirements, write spec",
    RalphState.ACT: "Action: implement code and tests per spec",
    RalphState.VERIFY: "Verify: run gates (TDD, security, review)",
    RalphState.REFLECT: "Reflect: analyze outcomes, capture learnings",
    RalphState.COMMIT: "Commit: all tasks complete, ready to commit",
    RalphState.ESCALATE: "Escalate: requires human decision",
    RalphState.ABORT: "Abort: context budget POOR, checkpoint and stop",
}


@dataclass
class StateTransition:
    """Record of a state transition for history."""
    from_state: RalphState
    to_state: RalphState
    timestamp: str
    trigger: str = ""


@dataclass
class StateViewState:
    """Mutable state for StateView."""
    current_state: RalphState = RalphState.PLAN
    retry_count: int = 0
    max_retries: int = 3
    context_tier: ContextTier = ContextTier.PEAK
    context_usage: float = 0.0
    transition_history: list[StateTransition] = field(default_factory=list)
    is_running: bool = False
    metrics: dict = field(default_factory=lambda: {
        "total_iterations": 0,
        "total_retries": 0,
        "total_escalations": 0,
        "start_time": None,
    })


class StateView:
    """Visualizes RalphLoop state machine with PLAN→ACT→VERIFY→REFLECT cycle.

    Shows:
        - Current state with color-coded highlight
        - State flow diagram with transition arrows
        - Retry counter for current task
        - State transition history
        - Runtime metrics

    Usage:
        view = StateView(console)
        view.update_state(RalphState.ACT, retry_count=0)
        view.render()  # Returns Panel for layout integration
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        on_state_change: Optional[Callable[[RalphState, RalphState], None]] = None,
    ):
        """Initialize StateView.

        Args:
            console: Rich Console instance. Creates new if None.
            on_state_change: Optional callback when state changes.
        """
        self.console = console or Console()
        self._state = StateViewState()
        self._on_state_change = on_state_change

    @property
    def current_state(self) -> RalphState:
        return self._state.current_state

    @property
    def retry_count(self) -> int:
        return self._state.retry_count

    @property
    def context_tier(self) -> ContextTier:
        return self._state.context_tier

    @property
    def is_running(self) -> bool:
        return self._state.is_running

    def update_state(
        self,
        new_state: RalphState,
        retry_count: Optional[int] = None,
        context_tier: Optional[ContextTier] = None,
        context_usage: Optional[float] = None,
        trigger: str = "",
        metrics: Optional[dict] = None,
    ) -> None:
        """Update the displayed state.

        Args:
            new_state: New RalphState to display.
            retry_count: Current retry count (None to keep existing).
            context_tier: Current context tier (None to keep existing).
            context_usage: Current context usage percentage.
            trigger: Description of what triggered this transition.
            metrics: Runtime metrics dict.
        """
        old_state = self._state.current_state
        self._state.current_state = new_state

        if retry_count is not None:
            self._state.retry_count = retry_count
        if context_tier is not None:
            self._state.context_tier = context_tier
        if context_usage is not None:
            self._state.context_usage = context_usage
        if metrics is not None:
            self._state.metrics.update(metrics)

        # Record transition in history
        if old_state != new_state:
            from datetime import datetime
            self._state.transition_history.append(StateTransition(
                from_state=old_state,
                to_state=new_state,
                timestamp=datetime.now().strftime("%H:%M:%S"),
                trigger=trigger,
            ))
            # Keep last 10 transitions
            if len(self._state.transition_history) > 10:
                self._state.transition_history.pop(0)

            # Fire callback
            if self._on_state_change:
                self._on_state_change(old_state, new_state)

    def set_running(self, running: bool) -> None:
        """Set the running state indicator."""
        self._state.is_running = running

    def get_transition_history_text(self) -> Text:
        """Get formatted transition history."""
        if not self._state.transition_history:
            return Text("  (no transitions yet)", style="dim")

        lines = []
        for t in self._state.transition_history[-5:]:  # Last 5
            arrow = "→"
            line = f"[{STATE_COLORS.get(t.from_state, 'white')}]{t.from_state.name}[/] {arrow} "
            line += f"[{STATE_COLORS.get(t.to_state, 'white')}]{t.to_state.name}[/]"
            if t.trigger:
                line += f" ({t.trigger})"
            lines.append(Text.from_markup(line))
        return Text("\n").join(lines) if lines else Text("  (no transitions yet)", style="dim")

    def _build_state_flow_diagram(self) -> Text:
        """Build the PLAN→ACT→VERIFY→REFLECT flow diagram."""
        states = [
            RalphState.PLAN,
            RalphState.ACT,
            RalphState.VERIFY,
            RalphState.REFLECT,
        ]

        segments = []
        for i, state in enumerate(states):
            is_active = state == self._state.current_state
            is_completed = state.value < self._state.current_state.value

            style = STATE_COLORS[state]
            if is_active:
                segments.append(f"[[{style}]{state.name}[/{style}]]")
            elif is_completed:
                segments.append(f"[{style}]{state.name}[/{style}]")
            else:
                segments.append(f"[dim]{state.name}[/dim]")

            if i < len(states) - 1:
                segments.append(" → ")

        return Text("  ".join(segments), justify="center")

    def _build_retry_display(self) -> Text:
        """Build retry count display."""
        dots = []
        for i in range(self._state.max_retries):
            if i < self._state.retry_count:
                dots.append("[red]●[/red]")
            else:
                dots.append("[dim]○[/dim]")
        return Text("  ".join(dots))

    def _build_metrics_table(self) -> Table:
        """Build runtime metrics table."""
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="dim")
        table.add_column(style="white")

        m = self._state.metrics
        table.add_row("Iterations:", str(m.get("total_iterations", 0)))
        table.add_row("Total Retries:", str(m.get("total_retries", 0)))
        table.add_row("Escalations:", str(m.get("total_escalations", 0)))

        if m.get("start_time"):
            table.add_row("Started:", str(m["start_time"])[:19])

        return table

    def render(self) -> Panel:
        """Render the state view as a Rich Panel.

        Returns:
            Panel ready for layout integration.
        """
        # State header with flow diagram
        state_flow = self._build_state_flow_diagram()

        # Current state info
        current_color = STATE_COLORS.get(self._state.current_state, "white")
        current_desc = STATE_DESCRIPTIONS.get(self._state.current_state, "")

        content_lines = [
            state_flow,
            Text(""),
            Text.from_markup(f"[bold {current_color}]▶ {self._state.current_state.name}[/bold {current_color}]"),
            Text.from_markup(f"[dim]{current_desc}[/dim]"),
            Text(""),
            Text.from_markup("[bold]Retries:[/bold] ") + self._build_retry_display(),
            Text(""),
        ]

        # Transition history
        content_lines.append(Text.from_markup("[bold]History:[/bold]"))
        content_lines.append(self.get_transition_history_text())
        content_lines.append(Text(""))

        # Metrics
        content_lines.append(Text.from_markup("[bold]Metrics:[/bold]"))
        content_lines.append(self._build_metrics_table())

        # Running indicator
        if self._state.is_running:
            content_lines.append(Text(""))
            content_lines.append(Text.from_markup("[green]● RUNNING[/green]"))
        else:
            content_lines.append(Text(""))
            content_lines.append(Text.from_markup("[dim]○ IDLE[/dim]"))

        return Panel(
            Text("\n").join(content_lines),
            title="[bold]RalphLoop State Machine[/bold]",
            border_style="cyan",
            box=box.ROUNDED,
        )


# Box style — use rich.box constants
state_view_box_style = rich_box.ROUNDED
