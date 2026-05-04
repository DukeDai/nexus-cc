"""Nexus TUI - Interactive Terminal User Interface for RalphLoop.

Main TUI application that integrates all view components:
    - StateView: RalphLoop state machine visualization
    - AgentView: Multi-agent status display
    - ContextView: Context budget meter
    - TaskView: Task queue display

Provides real-time updates via Rich Live display and integrates
with RalphLoop via on_state_change, on_warning, on_escalation callbacks.

Usage:
    from .app import NexusTUI

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

from src.ralphloop.states import RalphState
from src.ralphloop.orchestrator import (
    RalphLoop,
    ContextTier,
    EscalationOption,
    Checkpoint,
)
from src.ralphloop.agent_loop import run_agent_loop, AgentLoopConfig, LoopResult
from src.ralphloop.implementation_context import ImplementationContext
from src.llm.client import LLMClient
from .state_view import StateView
from .agent_view import AgentView, AgentStatus
from .context_view import ContextView
from .task_view import TaskView
from .input_handler import CommandInputHandler, InputMode, Command
from .approval import ApprovalWorkflow, ApprovalType, ApprovalRequest


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
    escalating: bool = False
    pending_approval: Optional[ApprovalRequest] = None
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
        tty_mode: bool = True,
        project_path: Optional[str] = None,
    ):
        """Initialize NexusTUI.

        Args:
            task_queue: List of task dicts to process.
            context_monitor: Callable returning context usage (0-100).
            checkpoint_dir: Optional directory for checkpoints.
            console: Rich Console instance.
            tty_mode: If True, enables interactive command input and blocking
                      escalation handling. If False, runs in headless mode
                      where callbacks return defaults immediately (for CI/scripts).
            project_path: Path to the project directory for agent execution.
        """
        self.console = console or Console(**DEFAULT_CONSOLE_CONFIG)
        self._state = NexusTUIState()
        self._tty_mode = tty_mode
        self.project_path = project_path or "."

        # Escalation state
        self._pending_escalation_event: Optional[threading.Event] = None
        self._pending_escalation_response: Optional[EscalationOption] = None
        self._pending_escalation_context: Optional[dict[str, Any]] = None

        # Command handlers
        self._commands = {
            'approve': self._cmd_approve,
            'reject': self._cmd_reject,
            'retry': self._cmd_retry,
            'skip': self._cmd_skip,
            'undo': self._cmd_undo,
            'status': self._cmd_status,
            'help': self._cmd_help,
            'quit': self._cmd_quit,
            'exit': self._cmd_quit,
        }

        # Input handler (only active in tty_mode)
        self._input_handler: Optional[CommandInputHandler] = None
        if self._tty_mode:
            self._input_handler = CommandInputHandler(
                on_command=self._handle_command,
                on_ctrl_c=self._handle_ctrl_c,
            )
            # Wire up commands for tab completion
            self._input_handler.commands = list(self._commands.keys())

        # Approval workflow (only active in tty_mode)
        self._approval: Optional[ApprovalWorkflow] = None
        self._pending_approval_event: Optional[threading.Event] = None
        if self._tty_mode:
            self._approval = ApprovalWorkflow(
                on_approve=self._on_approval_approve,
                on_reject=self._on_approval_reject,
            )

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

        # Set up real agent execution infrastructure
        self._setup_agent_execution()
        self._agent_context = None  # per-task context

        # Initialize RalphLoop with TUI callbacks
        self._ralphloop = RalphLoop(
            task_queue=task_queue,
            context_monitor=context_monitor,
            checkpoint_dir=checkpoint_dir,
            on_state_change=self._on_ralph_state_change_wrapper,
            on_escalation=self._on_escalation_wrapper,
            on_warning=self._on_context_warning_wrapper,
            agent_executor=self._create_agent_executor(),
            on_approval_request=self._on_approval_request_wrapper if self._tty_mode else None,
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

        In tty_mode: blocks until user selects an escalation option via
        command input (1-4 keys or typed command).

        In headless mode: returns REWRITE immediately.
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

        # Headless mode: return default immediately
        if not self._tty_mode:
            return EscalationOption.REWRITE

        # Interactive mode: block and wait for user input
        self._state.escalating = True
        self._pending_escalation_event = threading.Event()
        self._pending_escalation_context = escalation_context
        self._input_handler.set_mode(InputMode.ESCALATION)

        self._pending_escalation_event.wait()
        self._pending_escalation_event = None
        self._state.escalating = False

        return self._pending_escalation_response or EscalationOption.ABANDON

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

    # ─── Approval Callbacks ───────────────────────────────────────────────────

    def _on_approval_approve(self, request: ApprovalRequest) -> None:
        """Called when user approves an approval request."""
        self._state.messages.append(f"Approved: {request.description}")

    def _on_approval_reject(self, request: ApprovalRequest) -> None:
        """Called when user rejects an approval request."""
        self._state.messages.append(f"Rejected: {request.description}")

    def _on_approval_request_wrapper(
        self,
        approval_type: str,
        description: str,
        details: dict,
    ) -> bool:
        """Request approval from user - blocks until responded.

        This is called from the RalphLoop thread and blocks until
        the user approves or rejects via command input.
        """
        at = ApprovalType.COMMIT if approval_type == "commit" else \
             ApprovalType.CONTEXT_THRESHOLD if approval_type == "context_threshold" else \
             ApprovalType.DANGEROUS_COMMAND if approval_type == "dangerous_command" else \
             ApprovalType.COMMIT
        request = ApprovalRequest(
            type=at,
            description=description,
            details=details,
            timestamp=datetime.now().isoformat(),
        )

        # Set paused state and wait for user response
        self._state.is_paused = True
        self._state.pending_approval = request

        result = self._approval.request_approval(request)

        self._state.is_paused = False
        self._state.pending_approval = None
        return result

    # ─── Command Handling ─────────────────────────────────────────────────────

    def _handle_command(self, cmd: Command) -> None:
        """Process a command from the input handler."""
        # Handle escalation response first
        if self._state.escalating and cmd.name in ('force-merge', 'rewrite', 'abandon', 'decompose'):
            self._pending_escalation_response = EscalationOption(cmd.name)
            self._pending_escalation_event.set()
            self._input_handler.set_mode(InputMode.LINE)
            self._state.messages.append(f"Escalation: {cmd.name} selected")
            return

        # Handle pending approval via ApprovalWorkflow
        if self._approval and self._approval.pending:
            if cmd.name in ('approve', 'yes', 'y'):
                self._approval.approve()
                self._state.is_paused = False
                return
            if cmd.name in ('reject', 'no', 'n'):
                self._approval.reject()
                self._state.is_paused = False
                return

        # Dispatch to registered commands
        handler = self._commands.get(cmd.name)
        if handler:
            handler(cmd.args)
        else:
            self._state.messages.append(f"Unknown command: {cmd.name}. Type 'help' for available commands.")

    def _handle_ctrl_c(self) -> None:
        """Handle Ctrl+C interrupt."""
        self._state.messages.append("Interrupted.")
        self.stop()

    # ─── Command Handlers ─────────────────────────────────────────────────────

    def _cmd_approve(self, args: list[str]) -> None:
        """Approve current operation."""
        self._state.messages.append("Approved.")

    def _cmd_reject(self, args: list[str]) -> None:
        """Reject current operation."""
        self._state.messages.append("Rejected.")

    def _cmd_retry(self, args: list[str]) -> None:
        """Retry current task."""
        if self._ralphloop:
            self._ralphloop.retry_count = 0
        self._state.messages.append("Retrying...")

    def _cmd_skip(self, args: list[str]) -> None:
        """Skip current task."""
        if self._ralphloop and self._ralphloop.task_index < len(self._ralphloop.task_queue):
            self._ralphloop.task_index += 1
        self._state.messages.append("Skipped.")

    def _cmd_undo(self, args: list[str]) -> None:
        """Undo last change via git."""
        self._state.messages.append("Undo not yet implemented.")

    def _cmd_status(self, args: list[str]) -> None:
        """Show current status."""
        if self._ralphloop:
            state_name = self._ralphloop.state.name if hasattr(self._ralphloop, 'state') else "UNKNOWN"
            task_idx = self._ralphloop.task_index + 1 if hasattr(self._ralphloop, 'task_index') else 0
            task_total = len(self._ralphloop.task_queue) if hasattr(self._ralphloop, 'task_queue') else 0
            self._state.messages.append(
                f"State: {state_name}, Task: {task_idx}/{task_total}"
            )

    def _cmd_help(self, args: list[str]) -> None:
        """Show available commands."""
        self._state.messages.append(
            "Commands: approve, reject, retry, skip, undo, status, help, quit"
        )

    def _cmd_quit(self, args: list[str]) -> None:
        """Exit the TUI."""
        self._state.messages.append("Quitting...")
        self.stop()

    # ─── Agent Helpers ──────────────────────────────────────────────────────

    def _get_agent_for_state(self, state: RalphState):
        """Map RalphState to AgentRole."""
        from src.agents.base import AgentRole

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

    def _setup_agent_execution(self) -> None:
        """Set up LLM client for real agent execution."""
        import os
        from pathlib import Path

        # Detect provider and model
        provider = os.environ.get("NEXUS_PROVIDER", "anthropic")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "")

        if not api_key:
            self._state.messages.append(
                "[red]Warning:[/red] ANTHROPIC_API_KEY not set. "
                "Run `export ANTHROPIC_API_KEY=sk-...` first."
            )

        self._llm = LLMClient(
            provider=provider,
            model="sonnet",
            api_key=api_key,
            base_url=base_url,
        )
        self._streaming = False

        # Agent loop config
        self._agent_config = AgentLoopConfig(
            max_turns=200,
            tool_timeout=30,
            context_window=200_000,
            stop_on_content=False,
            streaming=self._streaming,
        )

    def _create_agent_executor(self) -> Callable[..., dict[str, Any]]:
        """Create agent executor that calls real run_agent_loop."""

        def executor(task: dict[str, Any], phase: RalphState) -> dict[str, Any]:
            """Real agent executor via run_agent_loop."""
            task_desc = task.get("description", task.get("requirements", "Unknown"))
            workdir = Path(self.project_path)

            # Update agent view to show active agent
            role_map = {
                RalphState.PLAN: AgentRole.SPECIFIER,
                RalphState.ACT: AgentRole.IMPLEMENTER,
                RalphState.VERIFY: AgentRole.REVIEWER,
                RalphState.REFLECT: AgentRole.REVIEWER,
            }
            role = role_map.get(phase)
            if role and hasattr(self, '_agent_view'):
                self._agent_view.update_agent(
                    role, status=AgentStatus.ACTIVE, current_task=f"{phase.name}: {task_desc[:40]}"
                )

            # Build prompt based on phase
            if phase == RalphState.PLAN:
                prompt = f"TASK: {task_desc}\n\nWork directory: {workdir}\n\nAnalyze the task and respond with a brief plan."
            elif phase == RalphState.ACT:
                prompt = f"TASK: {task_desc}\n\nWork directory: {workdir}\n\nExecute the plan. Use tools to create/modify files."
            elif phase == RalphState.VERIFY:
                prompt = f"TASK: {task_desc}\n\nWork directory: {workdir}\n\nVerify the results. Check for errors."
            elif phase == RalphState.REFLECT:
                prompt = f"TASK: {task_desc}\n\nWork directory: {workdir}\n\nAnalyze outcomes and capture learnings."
            else:
                prompt = task_desc

            # Create fresh context for this execution
            ctx = ImplementationContext(
                task=task_desc,
                messages=[],
                tool_results=[],
                test_results=[],
                error_log=[],
            )

            try:
                result: LoopResult = run_agent_loop(
                    task=prompt,
                    llm_client=self._llm,
                    context=ctx,
                    config=self._agent_config,
                    workdir=workdir,
                    tools=None,  # use default tools
                    streaming_callback=None,
                    wal=None,
                    tdd_enforcer=None,
                )

                # Update agent view
                if role and hasattr(self, '_agent_view'):
                    self._agent_view.update_agent(
                        role,
                        status=AgentStatus.SUCCESS if result.complete else AgentStatus.ERROR,
                        last_result=result.final_content[:50] if result.final_content else "",
                    )

                return {
                    "success": result.complete,
                    "error": None if result.complete else "Agent loop incomplete",
                    "result": result.final_content or "",
                    "dangerous_commands": self._detect_dangerous(result.tool_calls) if result.tool_calls else [],
                }
            except Exception as e:
                error_str = str(e)
                # Surface API key errors clearly
                if "api_key" in error_str.lower() or "authentication" in error_str.lower():
                    self._state.messages.append(f"[red]API Error:[/red] Set ANTHROPIC_API_KEY env variable")
                elif "connection" in error_str.lower() or "network" in error_str.lower():
                    self._state.messages.append(f"[red]Network Error:[/red] {error_str[:60]}")
                else:
                    self._state.messages.append(f"[red]Error:[/red] {error_str[:80]}")

                if role and hasattr(self, '_agent_view'):
                    self._agent_view.update_agent(
                        role, status=AgentStatus.ERROR, errors=[error_str[:50]]
                    )
                return {"success": False, "error": error_str, "result": None}

        return executor

    def _detect_dangerous(self, tool_calls: list) -> list[str]:
        """Detect dangerous commands in tool calls."""
        dangerous = []
        dangerous_patterns = ["rm -rf", "git push --force", "drop table", "DELETE FROM", "format disk"]
        for tc in tool_calls or []:
            name = tc.get("name", "")
            args = str(tc.get("arguments", ""))
            for pat in dangerous_patterns:
                if pat in args:
                    dangerous.append(args)
                    break
        return dangerous

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
        """Build the footer with messages, hints, and REPL-style command input."""
        from datetime import datetime

        lines = []

        # Show command palette in COMMAND mode (Ctrl+P)
        if self._input_handler and self._input_handler.mode == InputMode.COMMAND:
            collecting = self._input_handler.collecting
            completions = self._input_handler.get_completions(collecting)

            lines.append(Text.from_markup("[bold cyan]Command Palette[/bold cyan] "
                "(Esc to cancel, Tab to complete)"))

            # Show input line
            input_display = f"> {collecting}_" if collecting else "> _"
            lines.append(Text.from_markup(f"[yellow]{input_display}[/yellow]"))

            # Show available commands (or matching completions)
            if completions:
                if collecting:
                    lines.append(Text.from_markup(
                        f"[dim]Completions:[/dim] [cyan]{', '.join(completions[:5])}[/cyan]"
                    ))
                else:
                    lines.append(Text.from_markup(
                        "[dim]Commands:[/dim] [cyan]" +
                        ", ".join(sorted(self._input_handler.commands)) +
                        "[/cyan]"
                    ))
            else:
                lines.append(Text.from_markup("[red]No matching commands[/red]"))

        # Show escalation hint
        elif self._state.escalating:
            lines.append(Text.from_markup(
                "[bold yellow]ESCALATION:[/bold yellow] "
                "Press [cyan]1[/cyan]=force-merge [cyan]2[/cyan]=rewrite "
                "[cyan]3[/cyan]=abandon [cyan]4[/cyan]=decompose"
            ))

        # Show pending approval hint
        elif self._state.is_paused:
            approval = self._state.pending_approval
            if approval:
                desc = approval.description[:60]
                lines.append(Text.from_markup(f"[bold yellow]Approval Required:[/bold yellow] {desc}"))
            lines.append(Text.from_markup(
                "Type [cyan]approve[/cyan](y) or [cyan]reject[/cyan](n)"
            ))

        # Show recent messages
        elif self._state.messages:
            for msg in self._state.messages[-2:]:
                lines.append(Text.from_markup(f"[yellow]⚠ {msg}[/yellow]"))

        # Show recent errors
        if self._state.error_log:
            for err in self._state.error_log[-1:]:
                lines.append(Text.from_markup(f"[red]✗ {err[:80]}[/red]"))

        if not lines:
            if self._tty_mode:
                lines.append(Text.from_markup("[dim]Ctrl+P for commands[/dim]"))
            else:
                lines.append(Text.from_markup("[dim]Press Ctrl+C to exit[/dim]"))

        # Timestamp
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # Dynamic height - taller in command palette mode
        is_command_mode = self._input_handler and self._input_handler.mode == InputMode.COMMAND
        height = 7 if is_command_mode else 5

        # Don't show timestamp in command palette mode to save space
        if not is_command_mode:
            lines.append(Text.from_markup(f"[dim]{timestamp}[/dim]"))

        return Panel(
            Text("\n").join(lines),
            border_style="dim" if not is_command_mode else "cyan",
            height=height,
        )

    def _build_input_bar(self) -> Panel:
        """Build a persistent command input bar at the bottom of the screen.

        This provides a Claude Code-like REPL experience where users can
        always see the command input line at the bottom.
        """
        is_command_mode = self._input_handler and self._input_handler.mode == InputMode.COMMAND

        # Get current input in LINE mode
        if self._input_handler and self._input_handler.mode == InputMode.LINE:
            collecting = self._input_handler.collecting
            input_text = f"> {collecting}_" if collecting else "> _"
        else:
            input_text = "> _"

        # Show command hints in LINE mode (non-blocking)
        if self._input_handler and self._input_handler.mode == InputMode.LINE:
            hints_text = "[dim]status | help | approve | reject | retry | skip | undo | quit | Ctrl+P palette[/dim]"
        else:
            hints_text = ""

        bar_text = Text.from_markup(
            f"[bold cyan]{input_text}[/bold cyan]  {hints_text}"
        )

        return Panel(
            bar_text,
            border_style="cyan" if is_command_mode else "dim",
            height=3,
        )

    def _build_layout(self) -> Layout:
        """Build the main layout."""
        layout = Layout()

        # Header
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=5),
            Layout(name="input_bar", size=3),
        )

        # Main area: 2x2 grid - split into left and right columns
        layout["main"].split_column(
            Layout(name="left_col"),
            Layout(name="right_col"),
        )

        # Left column: state + context
        layout["left_col"].split_column(
            Layout(name="state", ratio=1),
            Layout(name="context", ratio=1),
        )

        # Right column: agents + tasks
        layout["right_col"].split_column(
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
        layout["input_bar"].update(self._build_input_bar())

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
            self._layout["input_bar"].update(self._build_input_bar())

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

        # Start input handler in tty mode
        if self._input_handler:
            self._input_handler.start()

        # Build layout
        self._layout = self._build_layout()

        def update_loop():
            """Background update loop for live refresh + command polling."""
            with Live(
                self._layout,
                console=self.console,
                refresh_per_second=10,
                transient=False,
            ) as live:
                self._live = live
                while self._state.is_running:
                    time.sleep(0.1)

                    # Poll and process commands
                    if self._input_handler:
                        while True:
                            cmd = self._input_handler.get_next_command()
                            if not cmd:
                                break
                            self._handle_command(cmd)

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

        # Stop input handler
        if self._input_handler:
            self._input_handler.stop()

        return getattr(self, "_ralphloop_result", {})

    def stop(self) -> None:
        """Stop the TUI and RalphLoop."""
        self._state.is_running = False
        if self._ralphloop:
            self._ralphloop.stop()
        if self._input_handler:
            self._input_handler.stop()

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
