"""PlanPanel - Textual Container showing a Plan as a Tree, with key bindings."""
from __future__ import annotations

from dataclasses import asdict

from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Tree

from ..agent.control import Command, CommandKind, ControlChannel
from ..agent.plan import Plan, PlanStep, PlanStepKind
from ..agent.events import (
    PlanStarted,
    StepCompleted,
    StepFailed,
    StepStarted,
    WalkEvent,
)
from ..llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy
from .step_edit_modal import StepEditModal


# --- v1.2 model-badge helpers -------------------------------------------------

# Map step.kind → ModelHint. SUBPLAN inherits the role's effective model via
# the policy's per_role lookup; ASK_USER doesn't invoke a model.
_STEP_KIND_TO_HINT: dict[PlanStepKind, ModelHint | None] = {
    PlanStepKind.TOOL: ModelHint.PLANNER,
    PlanStepKind.VERIFY: ModelHint.VERIFIER_REVIEW,
    PlanStepKind.CRITIQUE: ModelHint.CRITIQUE,
    PlanStepKind.SUBPLAN: None,  # resolved via step.role.name
    PlanStepKind.ASK_USER: None,  # no model call
}


def _short_model_tag(model: str) -> str:
    """Map a full model name to a short badge string.

    Examples:
        'claude-sonnet-4-6' -> 'Sonnet'
        'claude-haiku-4-5'  -> 'Haiku'
        'claude-opus-4-8'   -> 'Opus'
        anything else       -> the basename after the last '-'
    """
    name = (model or "").lower()
    if "sonnet" in name:
        return "Sonnet"
    if "haiku" in name:
        return "Haiku"
    if "opus" in name:
        return "Opus"
    # Fallback: last hyphen-separated token (e.g. "gpt-4o-mini" -> "mini").
    return model.split("-")[-1] if model else "?"


def _resolve_step_model(step: PlanStep, policy: ModelPolicy | None) -> str | None:
    """Resolve the model name that will be used to execute ``step``.

    Returns ``None`` when the step doesn't trigger an LLM call (e.g.
    ASK_USER) or when no policy is attached.

    Precedence (mirrors ModelPolicy.resolve + step.role override):
        1. step.role.name → policy.per_role[role]  (if SUBPLAN and role set)
        2. step.kind → ModelHint → policy.resolve(hint)

    Args:
        step:   The PlanStep to resolve a model for.
        policy: Active ModelPolicy, or None to skip the badge.
    """
    # TODO: implement the resolution logic described above.
    # - For SUBPLAN steps with a role, look up the role name in
    #   policy.per_role first; if absent, fall back to the role's
    #   tier-derived name from DEFAULT_POLICY[ModelHint.PLANNER].
    # - For other step kinds, map step.kind via _STEP_KIND_TO_HINT and
    #   call policy.resolve(hint) (or DEFAULT_POLICY[hint] if no policy).
    # - Return None for ASK_USER or when no model can be resolved.
    raise NotImplementedError("TODO: implement step → model resolution")


# -----------------------------------------------------------------------------


class PlanPanel(Container):
    """Left pane: renders the Plan as a Tree of steps.

    Subscribes to WalkEvents through NexusApp's single dispatcher
    (no per-panel set_interval — the dispatcher fixes the multi-panel
    race that previously dropped events on the floor).

    Key bindings dispatch Commands back through the channel so the
    AgentRuntime can react (approve, reject, pause, resume, abort).
    """

    # The panel owns plan-level key bindings (a/r/e/d/i). It must be
    # focusable so those bindings actually receive key events — without
    # this, the user can't trigger approve/reject from the keyboard.
    can_focus = True

    BINDINGS = [
        ("a", "approve", "Approve"),
        ("r", "reject", "Reject"),
        ("e", "edit_step", "Edit"),
        ("d", "delete_step", "Delete"),
        ("i", "insert_step", "Insert"),
        # j/k mirror Tree's built-in cursor movement; hidden from footer.
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        ("J", "move_down", "Move down"),
        ("K", "move_up", "Move up"),
        ("p", "pause", "Pause"),
        ("P", "resume", "Resume"),
        ("x", "abort", "Abort"),
    ]

    DEFAULT_CSS = """
    PlanPanel {
        height: 100%;
    }
    PlanPanel Tree {
        height: 100%;
    }
    """

    @property
    def tree(self) -> Tree:
        """Public alias for tests / external callers (mirrors plan naming)."""
        return self.plan_tree

    def __init__(
        self,
        *,
        channel: ControlChannel,
        plan: Plan | None = None,
        policy: ModelPolicy | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.channel = channel
        self.plan: Plan | None = plan
        self.policy = policy
        self.plan_tree: Tree = Tree("Plan")

    def compose(self):
        yield self.plan_tree

    def on_mount(self) -> None:
        # Subscribe to every event type this panel cares about. The
        # single NexusApp dispatcher delivers events to all subscribers.
        self.app.subscribe_event(PlanStarted, self._handle_event)
        self.app.subscribe_event(StepStarted, self._handle_event)
        self.app.subscribe_event(StepCompleted, self._handle_event)
        self.app.subscribe_event(StepFailed, self._handle_event)

    # ------------------------------------------------------------------ events

    def _handle_event(self, event: WalkEvent) -> None:
        """Dispatch a single WalkEvent to the right tree mutator."""
        if isinstance(event, PlanStarted):
            self._render_plan(event.plan)
        elif isinstance(event, StepStarted):
            self._mark_step(event.step.id, "▶")
        elif isinstance(event, StepCompleted):
            self._mark_step(event.step.id, "✓")
        elif isinstance(event, StepFailed):
            self._mark_step(event.step.id, "✗")
        # Other events are acknowledged but not rendered in this panel.

    # ------------------------------------------------------------------- tree

    def _render_plan(self, plan) -> None:
        """Reset the tree and add one leaf per step."""
        self.plan_tree.reset(plan.spec or "Plan")
        for step in plan.steps:
            self.plan_tree.root.add_leaf(
                self._step_label(step), data={"step_id": step.id}
            )

    def _step_label(self, step, marker: str = " ") -> str:
        """Format a step's display label with an optional model badge.

        Format: "{marker} {kind}: {intent}  [model]" when a model is
        resolvable, else the bare label.
        """
        intent = (step.intent or "")[:50]
        base = f"{marker} {step.kind.value}: {intent}"
        try:
            model = _resolve_step_model(step, self.policy)
        except NotImplementedError:
            model = None
        except Exception:
            model = None
        if model is None:
            return base
        return f"{base}  [{_short_model_tag(model)}]"

    def _mark_step(self, step_id: str, marker: str) -> None:
        """Find the node with the given step_id and prepend the marker."""
        for node in self.plan_tree.root.children:
            data = node.data or {}
            if data.get("step_id") == step_id:
                # Re-derive the label without the leading marker character.
                old = str(node.label)
                stripped = old.lstrip()
                # The original label was "{marker} {kind}: {intent}" where
                # marker was " " or one of ▶/✓/✗. Strip the first character
                # and re-prefix with the new marker.
                if len(stripped) > 1 and stripped[0] in (" ", "▶", "✓", "✗"):
                    rest = stripped[1:].lstrip()
                else:
                    rest = stripped
                # Preserve the trailing [model] badge if present.
                badge = ""
                if "[" in rest and rest.endswith("]"):
                    bracket = rest.rfind("[")
                    badge = " " + rest[bracket:]
                    rest = rest[:bracket].rstrip()
                node.set_label(f"{marker} {rest}{badge}")
                return

    # ------------------------------------------------------------------ subplan

    def render_step(self, step: PlanStep, depth: int = 0) -> str:
        """Render a single step as a string, with SUBPLAN getting role label."""
        indent = "  " * depth
        if step.kind == PlanStepKind.SUBPLAN:
            role_name = step.role.name if step.role else "?"
            task_preview = (step.tool or "")[:40]
            return f"{indent}▸ SUBPLAN ({role_name}) — {task_preview!r}"
        # Fallback for other step kinds
        return f"{indent}{step.kind.value}: {step.intent or ''}"

    def render_plan_tree(self) -> str:
        """Render the full plan as a newline-separated string."""
        if not self.plan:
            return ""
        lines = []
        for step in self.plan.steps:
            lines.append(self.render_step(step))
        return "\n".join(lines)

    def _mark_step(self, step_id: str, marker: str) -> None:
        """Find the node with the given step_id and prepend the marker."""
        for node in self.plan_tree.root.children:
            data = node.data or {}
            if data.get("step_id") == step_id:
                # Re-derive the label without the leading marker character.
                old = str(node.label)
                stripped = old.lstrip()
                # The original label was "{marker} {kind}: {intent}" where
                # marker was " " or one of ▶/✓/✗. Strip the first character
                # and re-prefix with the new marker.
                if len(stripped) > 1 and stripped[0] in (" ", "▶", "✓", "✗"):
                    rest = stripped[1:].lstrip()
                else:
                    rest = stripped
                node.set_label(f"{marker} {rest}")
                return

    # ----------------------------------------------------------------- actions

    def _put_command(self, cmd: Command) -> None:
        self.channel._commands.put_nowait(cmd)

    def action_approve(self) -> None:
        self._put_command(Command(CommandKind.APPROVE_PLAN))

    def action_reject(self) -> None:
        self._put_command(Command(CommandKind.REJECT_PLAN))

    def action_pause(self) -> None:
        self.channel.pause()

    def action_resume(self) -> None:
        self.channel.resume()

    def action_abort(self) -> None:
        self._put_command(Command(CommandKind.ABORT))

    # ---- stubs: implemented properly in Task 16+ ----
    def action_edit_step(self) -> None:
        """Open StepEditModal for the currently focused tree node, then
        enqueue an EDIT_STEP command carrying the new step on Save."""
        node = self.plan_tree.cursor_node
        if node is None or node.data is None:
            self.app.bell()
            return
        step_id = (node.data or {}).get("step_id")
        plan = getattr(self.app, "_current_plan", None)
        if not plan or not step_id:
            self.app.bell()
            return
        step = plan.find_step(step_id)
        if step is None:
            self.app.bell()
            return

        def _on_save(new_step) -> None:
            self.channel._commands.put_nowait(
                Command(
                    CommandKind.EDIT_STEP,
                    payload={
                        "step_id": new_step.id,
                        "step": asdict(new_step),
                    },
                )
            )

        self.app.push_screen(StepEditModal(step=step, on_save=_on_save))

    def action_delete_step(self) -> None:
        """Enqueue a REMOVE_STEP command for the focused step (modal: v1.1)."""
        node = self.plan_tree.cursor_node
        if node is None or node.data is None:
            self.app.bell()
            return
        step_id = (node.data or {}).get("step_id")
        if not step_id:
            self.app.bell()
            return
        self.channel._commands.put_nowait(
            Command(CommandKind.REMOVE_STEP, payload={"step_id": step_id})
        )

    def action_insert_step(self) -> None:
        """Enqueue an INSERT_STEP command (full modal: v1.1)."""
        plan = getattr(self.app, "_current_plan", None)
        if plan is None:
            self.app.bell()
            return
        self.channel._commands.put_nowait(
            Command(CommandKind.INSERT_STEP, payload={"after_step_id": None})
        )

    def action_move_down(self) -> None:
        self.app.bell()

    def action_move_up(self) -> None:
        self.app.bell()
