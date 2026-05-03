"""Nexus TUI - Interactive Terminal User Interface for RalphLoop.

Main TUI application that integrates all view components:
    - StateView: RalphLoop state machine visualization
    - AgentView: Multi-agent status display
    - ContextView: Context budget meter
    - TaskView: Task queue display

Provides real-time updates via Rich Live display and integrates
with RalphLoop via on_state_change, on_warning, on_escalation callbacks.

Usage:
    from tui.app import NexusTUI

    tui = NexusTUI(
        task_queue=[...],
        context_monitor=lambda: 35.0,
    )
    tui.run()  # blocking
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Any

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.screen import Screen
from rich.text import Text
from rich.live import Live

from ralphloop.states import RalphState
from ralphloop.orchestrator import (
    RalphLoop,
    ContextTier,
    EscalationOption,
    Checkpoint,
)
from tui.state_view import StateView
from tui.agent_view import AgentView, AgentStatus
from tui.context_view import ContextView
from tui.task_view import TaskView


# ─── Console Configuration ─────────────────────────────────────────────────────

DEFAULT_CONSOLE_CONFIG = {
    "force_terminal": True,
    "no_color": False,
    "tab_size": 4,
}


# ─── Main TUI Application ─────────────────────────────────────────────────────

@dataclass
class NexusTUIState:
    """Global TUI state."""
    is_running: bool = False
    is_paused: bool = False
    ralphloop: Optional[RalphLoop] = None
    start_time: Optional[datetime] = None
    error_log: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


class NexusTUI:
    """Interactive TUI for RalphLoop orchestration.

    Integrates:
        - StateView: RalphLoop state machine (PLAN→ACT→VERIFY→REFLECT)
        - AgentView: Multi-agent status
        - ContextView: Context budget meter
        - TaskView: Task queue display

    Usage:
        tui = NexusTUI(task_queue=[...], context_monitor=lambda: 35.0)
        tui.run()
    """

    def __init__(
        self,
        task_queue: list[dict[str, Any]],
        context_monitor: Callable[[], float],
        checkpoint_dir: Optional[str] = None,
        console: Optional[Console] = None,
    ):
        """Initialize NexusTUI.

        Args:
            task_queue: List of task dicts to process.
            context_monitor: Callable returning context usage (0-100).
            checkpoint_dir: Optional directory for checkpoints.
            console: Rich Console instance.
        """
        self.console = console or Console(**DEFAULT_CONSOLE_CONFIG)
        self._state = NexusTUIState()

        # Initialize views
        self._state_view = StateView(
            console=self.console,
            on_state_change=self._on_ralph_state_change,
        )
        self._agent_view = AgentView(console=self.console)
        self._context_view = ContextView(
            console=self.console,
            on_warning=self._on_context_warning,
        )
        self._task_view = TaskView(console=self.console)

        # Set initial tasks
        self._task_view.set_tasks(task_queue)

        # Initialize RalphLoop with TUI callbacks
        self._ralphloop = RalphLoop(
            task_queue=task_queue,
            context_monitor=context_monitor,
            checkpoint_dir=checkpoint_dir,
            on_state_change=self._on_ralph_state_change_wrapper,
            on_escalation=self._on_escalation_wrapper,
            on_warning=self._on_context_warning_wrapper,
            agent_executor=self._create_agent_executor(),
        )
        self._state.ralphloop = self._ralphloop

        # Layout
        self._layout: Optional[Layout] = None
        self._live: Optional[Live] = None

        # Thread safety
        self._lock = threading.Lock()

    # ─── RalphLoop Callbacks ─────────────────────────────────────────────────

    def _on_ralph_state_change_wrapper(
        self,
        old_state: RalphState,
        new_state: RalphState,
    ) -> None:
        """Wrapper for RalphLoop on_state_change callback.

        Updates views in a thread-safe manner.
        """
        def update():
            with self._lock:
                # Update state view
                self._state_view.update_state(
                    new_state,
                    retry_count=self._ralphloop.retry_count,
                    context_tier=self._ralphloop.context_tier,
                    context_usage=self._ralphloop.context_usage,
                    trigger=f"{old_state.name}→{new_state.name}",
                    metrics={
                        "total_iterations": self._ralphloop.metrics.total_iterations,
                        "total_retries": self._ralphloop.metrics.total_retries,
                        "total_escalations": self._ralphloop.metrics.total_escalations,
                        "start_time": self._ralphloop.metrics.start_time,
                    },
                )

                # Update task view
                self._task_view.update_current(self._ralphloop.task_index)

                # Update context view
                self._context_view.update(
                    usage_percent=self._ralphloop.context_usage,
                    tier=self._ralphloop.context_tier,
                )

                # Update agent status based on state
                self._update_agents_for_state(new_state)

        # Schedule update
        self.console.call_later(update)

    def _on_escalation_wrapper(self, escalation_context: dict[str, Any]) -> EscalationOption:
        """Wrapper for RalphLoop on_escalation callback.

        Shows escalation prompt in TUI and waits for user response.
        """
        task = escalation_context.get("task", {})
        error_log = escalation_context.get("error_log", [])

        # Log the escalation
        self._state.error_log.append(
            f"ESCALATION: Task '{task.get('description', 'unknown')}' "
            f"after {escalation_context.get('retry_count', 0)} retries"
        )

        # Update views
        self._state_view.update_state(
            RalphState.ESCALATE,
            trigger="MAX_RETRIES_EXCEEDED",
        )

        self._agent_view.update_agent(
            self._get_agent_for_state(RalphState.ESCALATE),
            status=AgentStatus.ERROR,
            errors=[f"Escalation: {e[:50]}" for e in error_log[-3:]],
        )

        # In demo mode, return ABANDON (user would select in real TUI)
        return EscalationOption.ABANDON

    def _on_context_warning_wrapper(self, tier: ContextTier, message: str) -> None:
        """Wrapper for RalphLoop on_warning callback.

        Displays warning in TUI.
        """
        self._state.messages.append(f"[{tier.name}] {message}")

        self._context_view.update(
            usage_percent=self._ralphloop.context_usage,
            tier=tier,
            warning_message=message,
        )

    def _on_ralph_state_change(self, old_state: RalphState, new_state: RalphState) -> None:
        """External callback for state changes (for external listeners)."""
        pass

    def _on_context_warning(self, tier: ContextTier, message: str) -> None:
        """External callback for context warnings (for external listeners)."""
        pass

    # ─── Agent Helpers ──────────────────────────────────────────────────────

    def _get_agent_for_state(self, state: RalphState):
        """Map RalphState to AgentRole."""
        from agents.base import AgentRole

        mapping = {
            RalphState.PLAN: AgentRole.SPECIFIER,
            RalphState.ACT: AgentRole.IMPLEMENTER,
            RalphState.VERIFY: AgentRole.REVIEWER,
            RalphState.REFLECT: AgentRole.REVIEWER,
        }
        return mapping.get(state, AgentRole.IMPLEMENTER)

    def _update_agents_for_state(self, state: RalphState) -> None:
        """Update agent statuses based on RalphLoop state."""
        self._agent_view.set_all_idle()

        if state == RalphState.PLAN:
            self._agent_view.update_agent(
                self._get_agent_for_state(state),
                status=AgentStatus.ACTIVE,
                current_task="Analyzing requirements, writing spec",
            )
        elif state == RalphState.ACT:
            self._agent_view.update_agent(
                self._get_agent_for_state(state),
                status=AgentStatus.ACTIVE,
                current_task="Implementing code per spec",
            )
        elif state == RalphState.VERIFY:
            self._agent_view.update_agent(
                self._get_agent_for_state(state),
                status=AgentStatus.ACTIVE,
                current_task="Running verification gates",
            )
        elif state == RalphState.REFLECT:
            self._agent_view.update_agent(
                self._get_agent_for_state(state),
                status=AgentStatus.ACTIVE,
                current_task="Analyzing outcomes",
            )
        elif state == RalphState.ESCALATE:
            self._agent_view.update_agent(
                self._get_agent_for_state(state),
                status=AgentStatus.WAITING,
                current_task="Waiting for escalation resolution",
            )

    def _create_agent_executor(self) -> Callable[..., dict[str, Any]]:
        """Create agent executor for RalphLoop.

        Returns a function that simulates agent execution for demo purposes.
        """
        def executor(task: dict[str, Any], phase: RalphState) -> dict[str, Any]:
            """Simulated agent executor.

            In production, this would dispatch to actual agents.
            """
            # Simulate work based on phase
            time.sleep(0.1)  # Brief delay

            task_desc = task.get("description", task.get("requirements", "Unknown"))

            if phase == RalphState.PLAN:
                return {
                    "success": True,
                    "error": None,
                    "result": f"Spec created for: {task_desc[:40]}",
                }
            elif phase == RalphState.ACT:
                return {
                    "success": True,
                    "error": None,
                    "result": f"Implemented: {task_desc[:40]}",
                }
            elif phase == RalphState.VERIFY:
                # Simulate occasional failures
                if "fail" in task_desc.lower():
                    return {
                        "success": False,
                        "error": "Verification failed: test assertion error",
                        "result": None,
                    }
                return {
                    "success": True,
                    "error": None,
                    "result": "All verification gates passed",
                }
            elif phase == RalphState.REFLECT:
                return {
                    "success": True,
                    "error": None,
                    "result": "Reflection complete, learnings captured",
                }

            return {"success": True, "error": None, "result": "Done"}

        return executor

    # ─── Layout Building ────────────────────────────────────────────────────

    def _build_header(self) -> Panel:
        """Build the header panel."""
        from rich.style import Style

        title = Text.from_markup(
            "[bold cyan]Nexus TUI[/bold cyan] — "
            "[dim]RalphLoop Orchestration Monitor[/dim]"
        )

        status = Text.from_markup("[green]● LIVE[/green]" if self._state.is_running else "[dim]○ IDLE[/dim]")

        header_text = Text.assemble(title, "  ", status)

        return Panel(
            header_text,
            border_style="cyan",
            height=3,
        )

    def _build_footer(self) -> Panel:
        """Build the footer with messages and timestamp."""
        from datetime import datetime

        lines = []

        # Show recent messages
        if self._state.messages:
            for msg in self._state.messages[-2:]:
                lines.append(Text.from_markup(f"[yellow]⚠ {msg}[/yellow]"))

        # Show recent errors
        if self._state.error_log:
            for err in self._state.error_log[-1:]:
                lines.append(Text.from_markup(f"[red]✗ {err[:80]}[/red]"))

        if not lines:
            lines.append(Text.from_markup("[dim]Press Ctrl+C to exit[/dim]"))

        # Timestamp
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        lines.append(Text.from_markup(f"[dim]{timestamp}[/dim]"))

        return Panel(
            Text("\n").join(lines),
            border_style="dim",
            height=5,
        )

    def _build_layout(self) -> Layout:
        """Build the main layout."""
        layout = Layout()

        # Header
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=5),
        )

        # Main area: 2x2 grid
        layout["main"].split_row(
            Layout(name="state", ratio=1),
            Layout(name="context", ratio=1),
        )
        layout["main"].split_row(
            Layout(name="agents", ratio=1),
            Layout(name="tasks", ratio=1),
        )

        # Assign panels to regions
        layout["header"].update(self._build_header())
        layout["state"].update(self._state_view.render())
        layout["context"].update(self._context_view.render())
        layout["agents"].update(self._agent_view.render())
        layout["tasks"].update(self._task_view.render())
        layout["footer"].update(self._build_footer())

        return layout

    def _refresh_layout(self) -> None:
        """Refresh the layout with current state."""
        if self._layout:
            # Update all panels
            self._layout["header"].update(self._build_header())
            self._layout["state"].update(self._state_view.render())
            self._layout["context"].update(self._context_view.render())
            self._layout["agents"].update(self._agent_view.render())
            self._layout["tasks"].update(self._task_view.render())
            self._layout["footer"].update(self._build_footer())

    # ─── Public API ─────────────────────────────────────────────────────────

    def run(self, blocking: bool = True) -> dict[str, Any]:
        """Run the TUI application.

        Args:
            blocking: If True, blocks until completion. If False, returns immediately.

        Returns:
            Dict with final state, metrics, and results.
        """
        self._state.is_running = True
        self._state.start_time = datetime.now()
        self._state_view.set_running(True)

        # Initial context update
        self._context_view.update(
            usage_percent=self._ralphloop.context_usage,
            tier=self._ralphloop.context_tier,
        )

        # Build layout
        self._layout = self._build_layout()

        def update_loop():
            """Background update loop for live refresh."""
            with Live(
                self._layout,
                console=self.console,
                refresh_per_second=10,
                transient=False,
            ) as live:
                self._live = live
                while self._state.is_running:
                    time.sleep(0.1)
                    with self._lock:
                        self._refresh_layout()
                    self.console.print("", end="")  # Trigger refresh

        # Start RalphLoop in background thread
        def ralphloop_thread():
            result = self._ralphloop.run()
            self._ralphloop_result = result
            self._state.is_running = False

        thread = threading.Thread(target=ralphloop_thread, daemon=True)
        thread.start()

        if blocking:
            try:
                update_loop()
            except KeyboardInterrupt:
                self._state.is_running = False
                self.console.print("\n[yellow]Shutting down...[/yellow]")
        else:
            # Return immediately, caller should handle threading
            return {"status": "started"}

        return getattr(self, "_ralphloop_result", {})

    def stop(self) -> None:
        """Stop the TUI and RalphLoop."""
        self._state.is_running = False
        if self._ralphloop:
            self._ralphloop.stop()

    @property
    def state_view(self) -> StateView:
        """Access the state view for external updates."""
        return self._state_view

    @property
    def agent_view(self) -> AgentView:
        """Access the agent view for external updates."""
        return self._agent_view

    @property
    def context_view(self) -> ContextView:
        """Access the context view for external updates."""
        return self._context_view

    @property
    def task_view(self) -> TaskView:
        """Access the task view for external updates."""
        return self._task_view


# ─── Demo Mode ────────────────────────────────────────────────────────────────

def demo_mode():
    """Run NexusTUI in demo mode with sample tasks."""
    console = Console(**DEFAULT_CONSOLE_CONFIG)

    # Sample task queue
    tasks = [
        {
            "id": "task_1",
            "description": "Implement user authentication module",
            "priority": 2,
        },
        {
            "id": "task_2",
            "description": "Add API rate limiting",
            "priority": 1,
        },
        {
            "id": "task_3",
            "description": "Write unit tests for auth",
            "priority": 1,
        },
        {
            "id": "task_4",
            "description": "Update documentation",
            "priority": 0,
        },
    ]

    # Simulated context monitor (oscillating usage)
    usage = [25.0, 35.0, 45.0, 55.0, 60.0, 65.0, 50.0, 40.0, 30.0]
    usage_index = [0]

    def context_monitor() -> float:
        val = usage[usage_index[0] % len(usage)]
        usage_index[0] += 1
        return val

    console.print("[bold cyan]Starting Nexus TUI Demo...[/bold cyan]\n")

    tui = NexusTUI(
        task_queue=tasks,
        context_monitor=context_monitor,
    )

    try:
        result = tui.run(blocking=True)
        console.print("\n[bold green]Demo completed![/bold green]")
        console.print(f"Result: {result}")
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        tui.stop()


if __name__ == "__main__":
    demo_mode()
