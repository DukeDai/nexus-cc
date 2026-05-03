"""Context Budget Meter with PEAK/GOOD/DEGRADING/POOR Tiers.

Displays context usage as a progress bar with tier-based coloring and
warnings. Integrates with RalphLoop's ContextTier enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn
from rich.style import Style
from rich.text import Text

from src.ralphloop.orchestrator import ContextTier


# ─── Tier Configuration ────────────────────────────────────────────────────────

TIER_THRESHOLDS = {
    ContextTier.PEAK: (0, 30),
    ContextTier.GOOD: (30, 50),
    ContextTier.DEGRADING: (50, 70),
    ContextTier.POOR: (70, 100),
}

TIER_COLORS = {
    ContextTier.PEAK: "green",
    ContextTier.GOOD: "cyan",
    ContextTier.DEGRADING: "yellow",
    ContextTier.POOR: "red",
}

TIER_DESCRIPTIONS = {
    ContextTier.PEAK: "Full operations, spawn parallel agents",
    ContextTier.GOOD: "Normal operations, prefer frontmatter",
    ContextTier.DEGRADING: "Economize, frontmatter-only, minimal inlining",
    ContextTier.POOR: "EMERGENCY: Checkpoint and stop",
}

TIER_ICONS = {
    ContextTier.PEAK: "●●●",
    ContextTier.GOOD: "●●○",
    ContextTier.DEGRADING: "●○○",
    ContextTier.POOR: "○○○",
}


@dataclass
class ContextViewState:
    """Mutable state for ContextView."""
    usage_percent: float = 0.0
    tier: ContextTier = ContextTier.PEAK
    warnings: list[str] = field(default_factory=list)
    is_over_budget: bool = False


class ContextView:
    """Context budget meter with PEAK/GOOD/DEGRADING/POOR tiers.

    Displays:
        - Horizontal progress bar showing context usage
        - Current tier with color coding
        - Tier thresholds and descriptions
        - Warning messages when tier changes to DEGRADING
        - Visual escalation as usage increases

    Usage:
        view = ContextView(console)
        view.update(usage_percent=45.0)
        panel = view.render()
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        on_warning: Optional[Callable[[ContextTier, str], None]] = None,
    ):
        """Initialize ContextView.

        Args:
            console: Rich Console instance. Creates new if None.
            on_warning: Optional callback when tier emits warning.
        """
        self.console = console or Console()
        self._state = ContextViewState()
        self._on_warning = on_warning
        self._last_tier: Optional[ContextTier] = None

    @property
    def usage_percent(self) -> float:
        return self._state.usage_percent

    @property
    def tier(self) -> ContextTier:
        return self._state.tier

    def update(
        self,
        usage_percent: float,
        tier: Optional[ContextTier] = None,
        warning_message: Optional[str] = None,
    ) -> None:
        """Update the context meter.

        Args:
            usage_percent: Current context usage (0-100).
            tier: Current context tier (auto-calculated if None).
            warning_message: Optional warning message to display.
        """
        self._state.usage_percent = usage_percent
        if tier is None:
            tier = ContextTier.from_usage(usage_percent)
        self._state.tier = tier
        self._state.is_over_budget = tier == ContextTier.POOR

        # Track tier changes and emit warnings
        if self._last_tier != tier:
            if tier.should_warn() and self._on_warning:
                msg = f"Context budget {usage_percent:.1f}% — entering DEGRADING mode"
                self._on_warning(tier, msg)
            self._last_tier = tier

        if warning_message:
            self._state.warnings.append(warning_message)
            # Keep last 5 warnings
            if len(self._state.warnings) > 5:
                self._state.warnings.pop(0)

    def clear_warnings(self) -> None:
        """Clear all warning messages."""
        self._state.warnings.clear()

    def _build_tier_bar(self) -> Text:
        """Build visual tier indicator bar."""
        tier_bar = []
        for tier in [ContextTier.PEAK, ContextTier.GOOD, ContextTier.DEGRADING, ContextTier.POOR]:
            threshold = TIER_THRESHOLDS[tier][1]
            color = TIER_COLORS[tier]
            tier_bar.append(f"[{color}]{threshold:3d}%[/]")

        return Text("  ".join(tier_bar), style="dim")

    def _build_progress_bar(self) -> Progress:
        """Build the main progress bar with tier coloring."""
        tier = self._state.tier
        bar_color = TIER_COLORS[tier]

        progress = Progress(
            TextColumn("[bold blue]{task.description}[/bold blue]"),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=self.console,
        )

        task_id = progress.add_task(
            f"[{bar_color}]Context[/]",
            completed=min(self._state.usage_percent, 100),
            total=100,
        )

        return progress

    def _build_tier_display(self) -> Text:
        """Build the tier badge with icons."""
        tier = self._state.tier
        color = TIER_COLORS[tier]
        icon = TIER_ICONS[tier]
        desc = TIER_DESCRIPTIONS[tier]

        tier_name = tier.name
        badge = f"[bold {color}]{tier_name}[/bold {color}]"

        return Text.from_markup(
            f"{badge}  [dim]{icon}[/dim]\n"
            f"[dim]{desc}[/dim]"
        )

    def _build_usage_meter(self) -> Text:
        """Build detailed usage breakdown."""
        lines = []
        for tier in [ContextTier.PEAK, ContextTier.GOOD, ContextTier.DEGRADING, ContextTier.POOR]:
            lo, hi = TIER_THRESHOLDS[tier]
            color = TIER_COLORS[tier]

            if self._state.usage_percent >= hi:
                # Full segment filled
                filled = "█" * 10
                lines.append(f"[{color}]{filled}[/{color}] {hi:3d}%")
            elif self._state.usage_percent >= lo:
                # Partial segment
                pct_in_tier = self._state.usage_percent - lo
                tier_range = hi - lo
                filled = int((pct_in_tier / tier_range) * 10)
                bar = "█" * filled + "░" * (10 - filled)
                lines.append(f"[{color}]{bar}[/{color}] {hi:3d}%")
            else:
                # Empty segment
                bar = "░" * 10
                lines.append(f"[dim]{bar}[/dim] {hi:3d}%")

        return Text("\n".join(lines), style="dim")

    def _build_warnings(self) -> list[Text]:
        """Build warning messages if any."""
        if not self._state.warnings:
            return []

        lines = []
        for w in self._state.warnings[-3:]:  # Last 3 warnings
            lines.append(Text.from_markup(f"[yellow]⚠ {w}[/yellow]"))
        return lines

    def render(self) -> Panel:
        """Render the context view as a Rich Panel.

        Returns:
            Panel ready for layout integration.
        """
        content_lines = [
            self._build_tier_bar(),
            Text(""),
        ]

        # Tier display
        content_lines.append(self._build_tier_display())
        content_lines.append(Text(""))

        # Usage meter
        content_lines.append(self._build_usage_meter())
        content_lines.append(Text(""))

        # Current percentage
        pct = self._state.usage_percent
        color = TIER_COLORS[self._state.tier]
        content_lines.append(Text.from_markup(
            f"[bold]Current:[/bold] [{color}]{pct:.1f}%[/] "
            f"({self._state.tier.name})"
        ))

        # Warnings
        warnings = self._build_warnings()
        if warnings:
            content_lines.append(Text(""))
            for w in warnings:
                content_lines.append(w)

        # Over budget indicator
        if self._state.is_over_budget:
            content_lines.append(Text(""))
            content_lines.append(Text.from_markup(
                "[bold red]⚠ EMERGENCY: Context budget critical![/bold red]"
            ))

        return Panel(
            Text("\n").join(content_lines),
            title="[bold]Context Budget[/bold]",
            border_style=TIER_COLORS[self._state.tier],
        )
