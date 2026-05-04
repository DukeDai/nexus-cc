"""Task Queue Display.

Shows the current task queue with progress indicators, task status,
priority, and estimated completion. Integrates with RalphLoop's task queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from io import StringIO
from typing import Optional, Callable, Any

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text


def _render_rich(renderable) -> str:
    """Render a Rich object to string using a StringIO buffer."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    console.print(renderable, end="")
    return buf.getvalue().rstrip("\n")


# ─── Task Status ───────────────────────────────────────────────────────────────

class TaskStatus(Enum):
    """Task queue item status."""
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


# ─── Priority Colors ──────────────────────────────────────────────────────────

PRIORITY_COLORS = {
    0: "dim",      # Low priority
    1: "white",    # Normal priority
    2: "yellow",   # High priority
    3: "red",      # Critical priority
}

PRIORITY_LABELS = {
    0: "LOW",
    1: "NORMAL",
    2: "HIGH",
    3: "CRITICAL",
}


@dataclass
class TaskInfo:
    """Information about a single task."""
    id: str
    description: str
    status: TaskStatus
    priority: int
    error: Optional[str] = None
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskViewState:
    """Mutable state for TaskView."""
    tasks: list[TaskInfo] = field(default_factory=list)
    current_index: int = 0
    completed_count: int = 0
    failed_count: int = 0


class TaskView:
    """Task queue display with progress.

    Shows:
        - Task list with status indicators
        - Current task highlighted
        - Progress bar (completed/total)
        - Priority indicators
        - Error messages for failed tasks
        - Queue summary

    Usage:
        view = TaskView(console)
        view.set_tasks([...])
        view.update_current(0)
        panel = view.render()
    """

    def __init__(self, console: Optional[Console] = None):
        """Initialize TaskView.

        Args:
            console: Rich Console instance. Creates new if None.
        """
        self.console = console or Console()
        self._state = TaskViewState()

    def set_tasks(self, tasks: list[dict[str, Any]]) -> None:
        """Set the task queue from list of task dicts.

        Args:
            tasks: List of task dictionaries with keys:
                - description: Task description
                - priority: Priority level (0-3)
                - id: Optional task ID
        """
        self._state.tasks = [
            TaskInfo(
                id=task.get("id", f"task_{i}"),
                description=task.get("description", task.get("requirements", "Unknown task")),
                status=TaskStatus.PENDING,
                priority=task.get("priority", 1),
            )
            for i, task in enumerate(tasks)
        ]
        self._state.current_index = 0
        self._state.completed_count = 0
        self._state.failed_count = 0

    def update_current(self, index: int) -> None:
        """Update the current task index.

        Args:
            index: New current task index.
        """
        if 0 <= index < len(self._state.tasks):
            self._state.current_index = index

            # Mark previous tasks as completed
            for i in range(index):
                if self._state.tasks[i].status == TaskStatus.PENDING:
                    self._state.tasks[i].status = TaskStatus.COMPLETED

    def mark_current_complete(self) -> None:
        """Mark the current task as completed and advance."""
        if self._state.current_index < len(self._state.tasks):
            task = self._state.tasks[self._state.current_index]
            task.status = TaskStatus.COMPLETED
            self._state.completed_count += 1
            self._state.current_index += 1

            # Mark new current as in progress
            if self._state.current_index < len(self._state.tasks):
                self._state.tasks[self._state.current_index].status = TaskStatus.IN_PROGRESS

    def mark_current_failed(self, error: Optional[str] = None) -> None:
        """Mark the current task as failed.

        Args:
            error: Optional error message to record.
        """
        if self._state.current_index < len(self._state.tasks):
            task = self._state.tasks[self._state.current_index]
            task.status = TaskStatus.FAILED
            task.error = error
            self._state.failed_count += 1

    def mark_current_skipped(self) -> None:
        """Mark the current task as skipped."""
        if self._state.current_index < len(self._state.tasks):
            task = self._state.tasks[self._state.current_index]
            task.status = TaskStatus.SKIPPED
            self._state.current_index += 1

    def update_task_status(
        self,
        index: int,
        status: TaskStatus,
        error: Optional[str] = None,
    ) -> None:
        """Update a specific task's status.

        Args:
            index: Task index.
            status: New task status.
            error: Optional error message.
        """
        if 0 <= index < len(self._state.tasks):
            task = self._state.tasks[index]
            task.status = status
            if error:
                task.error = error
            if status == TaskStatus.COMPLETED:
                self._state.completed_count += 1
            elif status == TaskStatus.FAILED:
                self._state.failed_count += 1

    def _get_status_icon(self, task: TaskInfo) -> str:
        """Get status icon for task."""
        icons = {
            TaskStatus.PENDING: "○",
            TaskStatus.IN_PROGRESS: "●",
            TaskStatus.COMPLETED: "✓",
            TaskStatus.FAILED: "✗",
            TaskStatus.SKIPPED: "⊘",
        }
        return icons.get(task.status, "○")

    def _get_status_color(self, task: TaskInfo) -> str:
        """Get color for task status."""
        colors = {
            TaskStatus.PENDING: "dim",
            TaskStatus.IN_PROGRESS: "green",
            TaskStatus.COMPLETED: "bold green",
            TaskStatus.FAILED: "bold red",
            TaskStatus.SKIPPED: "yellow",
        }
        return colors.get(task.status, "dim")

    def _get_priority_badge(self, priority: int) -> Text:
        """Get priority badge."""
        color = PRIORITY_COLORS.get(priority, "dim")
        label = PRIORITY_LABELS.get(priority, "NORMAL")
        return Text.from_markup(f"[{color}][{label}][/{color}]")

    def _build_task_table(self) -> Table:
        """Build the task queue table."""
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=None,
            pad_edge=False,
            min_width=60,
        )

        table.add_column("#", style="dim", width=3)
        table.add_column("Status", width=6)
        table.add_column("Task", min_width=40)
        table.add_column("Pri", width=7)

        for i, task in enumerate(self._state.tasks):
            is_current = i == self._state.current_index
            status_icon = self._get_status_icon(task)
            status_color = self._get_status_color(task)

            # Build task text
            if is_current:
                desc_style = "bold white"
                prefix = "▶ "
            else:
                desc_style = ""
                prefix = "  "

            desc_text = Text.from_markup(
                f"[{status_color}]{status_icon}[/{status_color}] "
                f"[{desc_style}]{prefix}{task.description[:50]}[/{desc_style}]"
            )

            priority_badge = self._get_priority_badge(task.priority)

            # Highlight current row
            row_style = "reverse" if is_current else ""

            table.add_row(
                str(i + 1),
                Text.from_markup(f"[{status_color}]{status_icon}[/{status_color}]"),
                desc_text,
                priority_badge,
                style=row_style,
            )

            # Add error message below failed tasks
            if task.status == TaskStatus.FAILED and task.error:
                error_text = Text.from_markup(f"  [red]✗ {task.error[:60]}[/red]")
                table.add_row("", "", error_text, "")

        return table

    def _build_progress_summary(self) -> Text:
        """Build progress summary line."""
        total = len(self._state.tasks)
        completed = self._state.completed_count
        failed = self._state.failed_count
        remaining = total - completed - failed

        if total == 0:
            return Text.from_markup("[dim]No tasks[/dim]")

        # Progress bar
        bar_width = 20
        filled = int((completed / total) * bar_width) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_width - filled)

        if completed == total:
            bar_color = "green"
        elif failed > 0:
            bar_color = "red"
        else:
            bar_color = "cyan"

        progress_line = Text.from_markup(
            f"[{bar_color}]{bar}[/{bar_color}] "
            f"[bold]{completed}/{total}[/bold] "
            f"([green]+{completed}[/green] "
            f"[red]-{failed}[/red] "
            f"[dim]~{remaining}[/dim])"
        )

        return progress_line

    def _build_queue_summary(self) -> Text:
        """Build queue summary text."""
        total = len(self._state.tasks)
        if total == 0:
            return Text("")

        current = self._state.current_index + 1 if self._state.current_index < total else total
        return Text.from_markup(
            f"[bold]Queue:[/bold] [cyan]{current}[/cyan] of [cyan]{total}[/cyan] "
            f"([dim]eta: {total - current} tasks remaining[/dim])"
        )

    def render(self) -> Panel:
        """Render the task view as a Rich Panel.

        Returns:
            Panel ready for layout integration.
        """
        lines = []
        lines.append(str(self._build_progress_summary()))
        lines.append("")
        lines.append(str(self._build_queue_summary()))
        lines.append("")
        # Render Table to string properly
        task_table = self._build_task_table()
        lines.append(_render_rich(task_table))

        return Panel(
            Text("\n").join([Text.from_markup(line) for line in lines]),
            title="[bold]Task Queue[/bold]",
            border_style="cyan",
        )
