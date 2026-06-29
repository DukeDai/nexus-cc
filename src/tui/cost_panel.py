"""TUI panel showing session cost rollup (v1.2 Model Router).

Reads from a CostTracker (the same instance the router emits to) and
renders per-model + per-hint totals. Subscribes to StepCompleted events
to refresh automatically after each walker step.
"""
from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive

from ..llm.cost_tracker import CostTracker
from ..llm.model_policy import ModelHint
from textual.widgets import Static


class CostPanel(Static):
    """Displays cost totals for the current session.

    Args:
        cost_tracker: Shared CostTracker (or None to render an empty panel).
                      Pass the same instance the ModelRouter emits to so
                      cost records appear here in real time.
    """

    COMPONENT_CLASSES = frozenset()
    can_focus = True

    # Reactive summary — recomputed when update_costs() is called.
    summary: reactive[dict] = reactive({})

    def __init__(self, *, cost_tracker: CostTracker | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cost_tracker = cost_tracker

    def render(self) -> Text:
        if self.cost_tracker is None:
            return Text("Cost tracking disabled (no router).", style="dim")
        if not self.summary or self.summary.get("calls", 0) == 0:
            return Text("No LLM calls yet this session.", style="dim")
        return self._format_summary(self.summary)

    def update_costs(self) -> None:
        """Recompute totals from the tracker and trigger a re-render.

        Safe to call on every WalkEvent — internally uses the tracker's
        in-memory ring buffer (no I/O).
        """
        if self.cost_tracker is None:
            return
        try:
            by_model = self.cost_tracker.aggregate_by("model")
            by_hint = self.cost_tracker.aggregate_by("hint")
        except Exception:
            # Tracker may have a malformed buffer; render empty rather than crash.
            self.summary = {}
            return
        # Overall total = sum of per-model cost_usd
        total = sum(b.get("cost_usd", 0.0) for b in by_model.values())
        call_count = sum(int(b.get("count", 0)) for b in by_model.values())
        prompt_total = sum(int(b.get("prompt_tokens", 0)) for b in by_model.values())
        completion_total = sum(int(b.get("completion_tokens", 0)) for b in by_model.values())
        self.summary = {
            "total": total,
            "calls": call_count,
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
            "by_model": by_model,
            "by_hint": by_hint,
        }

    @staticmethod
    def _format_summary(s: dict) -> Text:
        """Render the summary dict as a multi-line Rich Text block."""
        lines: list[str] = []
        lines.append(
            f"[bold]${s['total']:.4f}[/bold]  "
            f"({s['calls']} calls, "
            f"{s['prompt_tokens']} in / {s['completion_tokens']} out tokens)"
        )
        if s.get("by_model"):
            lines.append("[dim]by model:[/dim]")
            for model, bucket in sorted(s["by_model"].items()):
                lines.append(
                    f"  {model}: ${bucket['cost_usd']:.4f} "
                    f"({int(bucket['count'])} calls)"
                )
        if s.get("by_hint"):
            lines.append("[dim]by hint:[/dim]")
            for hint, bucket in sorted(s["by_hint"].items()):
                lines.append(
                    f"  {hint}: ${bucket['cost_usd']:.4f} "
                    f"({int(bucket['count'])} calls)"
                )
        return Text.from_markup("\n".join(lines))


# Convenience: a stable list of ModelHint values for tests / introspection.
ALL_HINTS: list[ModelHint] = list(ModelHint)
