"""NexusApp - Textual TUI for plan-first Nexus."""
from __future__ import annotations

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer

from ..agent.control import ControlChannel
from .execution_panel import ExecutionPanel
from .plan_panel import PlanPanel
from .tool_output_panel import ToolOutputPanel


class NexusApp(App):
    """Textual shell hosting the plan pane, execution log, and tool output.

    Tasks 14-19 progressively fill the four named panes with their own
    widgets. The app starts as a skeleton that already mounts Header/Footer
    and reserves space for each pane via CSS.
    """

    CSS_PATH = "styles.tcss"
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("?", "help", "Help")]

    def __init__(self, *, channel: ControlChannel, runtime=None) -> None:
        super().__init__()
        self.channel = channel
        self.runtime = runtime
        self._walk_task = None
        self._current_plan = None

    def compose(self):
        yield Header()
        with Horizontal():
            yield PlanPanel(channel=self.channel, id="plan-pane")
            with Vertical(id="right-pane"):
                yield ExecutionPanel(channel=self.channel, id="execution-pane")
                yield ToolOutputPanel(channel=self.channel, id="tool-output-pane")
        yield Footer()