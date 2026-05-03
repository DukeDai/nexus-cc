"""Multi-Agent Status Display.

Shows status of all Nexus agents: Specifier, Implementer, Reviewer, Security.
Each agent shows state, model tier, and activity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum, auto

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from ..agents.base import AgentRole, ModelTier


# ─── Agent Status Colors ───────────────────────────────────────────────────────

ROLE_COLORS = {
    AgentRole.SPECIFIER: "cyan",
    AgentRole.IMPLEMENTER: "green",
    AgentRole.REVIEWER: "yellow",
    AgentRole.SECURITY: "red",
}

MODEL_TIER_COLORS = {
    ModelTier.FAST: "dim",
    ModelTier.SONNET: "cyan",
    ModelTier.OPUS: "bold magenta",
}

STATUS_ICONS = {
    "idle": "○",
    "active": "●",
    "success": "✓",
    "error": "✗",
    "waiting": "◐",
}


class AgentStatus(Enum):
    """Agent operational status."""
    IDLE = auto()
    ACTIVE = auto()
    SUCCESS = auto()
    ERROR = auto()
    WAITING = auto()


@dataclass
class AgentInfo:
    """Information about a single agent."""
    role: AgentRole
    model_tier: ModelTier
    status: AgentStatus
    current_task: str = ""
    last_result: str = ""
    confidence: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class AgentViewState:
    """Mutable state for AgentView."""
    agents: dict[AgentRole, AgentInfo] = field(default_factory=lambda: {
        AgentRole.SPECIFIER: AgentInfo(
            role=AgentRole.SPECIFIER,
            model_tier=ModelTier.SONNET,
            status=AgentStatus.IDLE,
        ),
        AgentRole.IMPLEMENTER: AgentInfo(
            role=AgentRole.IMPLEMENTER,
            model_tier=ModelTier.SONNET,
            status=AgentStatus.IDLE,
        ),
        AgentRole.REVIEWER: AgentInfo(
            role=AgentRole.REVIEWER,
            model_tier=ModelTier.SONNET,
            status=AgentStatus.IDLE,
        ),
        AgentRole.SECURITY: AgentInfo(
            role=AgentRole.SECURITY,
            model_tier=ModelTier.FAST,
            status=AgentStatus.IDLE,
        ),
    })


class AgentView:
    """Multi-agent status display.

    Shows all Nexus agents with their:
        - Role and model tier
        - Current status (idle/active/success/error/waiting)
        - Current task description
        - Last result or errors

    Usage:
        view = AgentView(console)
        view.update_status(AgentRole.IMPLEMENTER, status=AgentStatus.ACTIVE)
        view.render()  # Returns Panel for layout integration
    """

    def __init__(self, console: Optional[Console] = None):
        """Initialize AgentView.

        Args:
            console: Rich Console instance. Creates new if None.
        """
        self.console = console or Console()
        self._state = AgentViewState()

    def update_agent(
        self,
        role: AgentRole,
        status: Optional[AgentStatus] = None,
        model_tier: Optional[ModelTier] = None,
        current_task: Optional[str] = None,
        last_result: Optional[str] = None,
        confidence: Optional[float] = None,
        errors: Optional[list[str]] = None,
    ) -> None:
        """Update agent information.

        Args:
            role: Agent role to update.
            status: New operational status.
            model_tier: Model tier (FAST/SONNET/OPUS).
            current_task: Description of current task.
            last_result: Result from last execution.
            confidence: Confidence score from last execution.
            errors: Error messages from last execution.
        """
        agent = self._state.agents.get(role)
        if agent is None:
            return

        if status is not None:
            agent.status = status
        if model_tier is not None:
            agent.model_tier = model_tier
        if current_task is not None:
            agent.current_task = current_task
        if last_result is not None:
            agent.last_result = last_result
        if confidence is not None:
            agent.confidence = confidence
        if errors is not None:
            agent.errors = errors

    def set_all_idle(self) -> None:
        """Set all agents to idle status."""
        for agent in self._state.agents.values():
            agent.status = AgentStatus.IDLE
            agent.current_task = ""

    def _build_agent_row(self, agent: AgentInfo) -> Table:
        """Build a table row for a single agent."""
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="dim")

        role_color = ROLE_COLORS[agent.role]
        status_icon = self._get_status_icon(agent.status)
        status_style = self._get_status_style(agent.status)

        # Role and tier
        tier_color = MODEL_TIER_COLORS[agent.model_tier]
        role_line = Text.from_markup(
            f"[bold {role_color}]{agent.role.name}[/bold {role_color}] "
            f"[{tier_color}]({agent.model_tier.name})[/{tier_color}]"
        )
        table.add_row(role_line)

        # Status
        status_line = Text.from_markup(
            f"{status_icon} [{status_style}]{agent.status.name}[/{status_style}]"
        )
        table.add_row(status_line)

        # Current task
        if agent.current_task:
            task_text = Text.from_markup(f"[dim]  → {agent.current_task[:40]}[/dim]")
            table.add_row(task_text)

        # Last result or errors
        if agent.errors:
            for err in agent.errors[-2:]:  # Last 2 errors
                table.add_row(Text.from_markup(f"[red]  ✗ {err[:50]}[/red]"))
        elif agent.last_result:
            result_preview = agent.last_result[:50]
            table.add_row(Text.from_markup(f"[dim]  → {result_preview}[/dim]"))

        # Confidence bar
        if agent.confidence > 0:
            conf_bar = self._build_confidence_bar(agent.confidence)
            table.add_row(conf_bar)

        return table

    def _get_status_icon(self, status: AgentStatus) -> str:
        """Get status icon for agent status."""
        icons = {
            AgentStatus.IDLE: "○",
            AgentStatus.ACTIVE: "●",
            AgentStatus.SUCCESS: "✓",
            AgentStatus.ERROR: "✗",
            AgentStatus.WAITING: "◐",
        }
        return icons.get(status, "○")

    def _get_status_style(self, status: AgentStatus) -> str:
        """Get Rich style for agent status."""
        styles = {
            AgentStatus.IDLE: "dim",
            AgentStatus.ACTIVE: "green",
            AgentStatus.SUCCESS: "bold green",
            AgentStatus.ERROR: "bold red",
            AgentStatus.WAITING: "yellow",
        }
        return styles.get(status, "dim")

    def _build_confidence_bar(self, confidence: float) -> Text:
        """Build a small confidence indicator bar."""
        filled = int(confidence * 10)
        bar = "▓" * filled + "░" * (10 - filled)

        if confidence >= 0.8:
            color = "green"
        elif confidence >= 0.5:
            color = "yellow"
        else:
            color = "red"

        return Text.from_markup(f"[{color}]{bar}[/{color}] {confidence:.0%}")

    def _build_summary(self) -> Text:
        """Build agent summary line."""
        total = len(self._state.agents)
        active = sum(1 for a in self._state.agents.values() if a.status == AgentStatus.ACTIVE)
        idle = sum(1 for a in self._state.agents.values() if a.status == AgentStatus.IDLE)
        errors = sum(1 for a in self._state.agents.values() if a.status == AgentStatus.ERROR)

        parts = []
        if active > 0:
            parts.append(f"[green]● {active} active[/green]")
        if idle > 0:
            parts.append(f"[dim]○ {idle} idle[/dim]")
        if errors > 0:
            parts.append(f"[red]✗ {errors} error[/red]")

        if not parts:
            return Text.from_markup("[dim]No agents[/dim]")

        return Text.from_markup("  ".join(parts))

    def render(self) -> Panel:
        """Render the agent view as a Rich Panel.

        Returns:
            Panel ready for layout integration.
        """
        content_lines = [
            self._build_summary(),
            Text(""),
        ]

        # Build agent tables
        for role in [AgentRole.SPECIFIER, AgentRole.IMPLEMENTER, AgentRole.REVIEWER, AgentRole.SECURITY]:
            agent = self._state.agents.get(role)
            if agent:
                content_lines.append(self._build_agent_row(agent))
                content_lines.append(Text(""))

        return Panel(
            Text("\n").join(content_lines),
            title="[bold]Multi-Agent Status[/bold]",
            border_style="cyan",
        )
