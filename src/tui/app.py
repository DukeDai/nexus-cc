"""NexusApp - Textual TUI for plan-first Nexus.

Wires the four named panes to the AgentRuntime through a single
ControlChannel. A subscriber-dispatcher pattern (one drainer in this
app, instead of one per panel) avoids the multi-panel race where two
intervals could try to ``try_recv_event`` for the same WalkEvent and
the loser silently dropped it.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer

from ..agent.control import Command, CommandKind, ControlChannel
from ..agent.events import PlanStarted, WalkEvent
from ..agent.plan import Plan, PlanStep, PlanStepKind, OnFailure
from .execution_panel import ExecutionPanel
from .new_task_modal import NewTaskModal
from .plan_panel import PlanPanel
from .recover_modal import RecoverModal
from .tool_output_panel import ToolOutputPanel


class NexusApp(App):
    """Textual shell hosting the plan pane, execution log, and tool output.

    Tasks 14-19 progressively fill the four named panes with their own
    widgets. The app starts as a skeleton that already mounts Header/Footer
    and reserves space for each pane via CSS.
    """

    CSS_PATH = "styles.tcss"
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("?", "help", "Help"),
        ("n", "new_task", "New task"),
    ]

    def __init__(self, *, channel: ControlChannel, runtime=None, wal=None) -> None:
        super().__init__()
        self.channel = channel
        self.runtime = runtime
        self._wal = wal
        self._walk_task: asyncio.Task | None = None
        self._current_plan: Plan | None = None

        # Subscriber dispatch: one drainer fan-outs WalkEvents to
        # all panels that subscribed for that event type. This is
        # the fix for the multi-panel race.
        self._event_subscribers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)
        self._command_subscribers: list[Callable[[Command], None]] = []

    # --------------------------------------------------------------- compose

    def compose(self):
        yield Header()
        with Horizontal():
            yield PlanPanel(channel=self.channel, id="plan-pane")
            with Vertical(id="right-pane"):
                yield ExecutionPanel(channel=self.channel, id="execution-pane")
                yield ToolOutputPanel(channel=self.channel, id="tool-output-pane")
        yield Footer()

    # ------------------------------------------------------------ on_mount

    def on_mount(self) -> None:
        # Single drainer for events at ~20Hz — fan-out to subscribers.
        self.set_interval(0.05, self._dispatch_events)
        # Single drainer for commands; forwards to runtime mutators.
        self.set_interval(0.05, self._drain_commands)
        # Defer the WAL recovery check until after compose() finishes so
        # the modal can mount correctly on top of the regular screen.
        self.call_after_refresh(self._maybe_offer_recovery)

    async def _maybe_offer_recovery(self) -> None:
        """If WAL has an unfinished plan, push RecoverModal.

        - No WAL attached -> return.
        - WAL empty / no recoverable plan -> return.
        - WAL raises -> silently fall through (broken WAL must not
          prevent the app from starting).
        """
        if self._wal is None:
            return
        try:
            recovered = await self._wal.recover()
        except Exception:
            return
        if recovered is None:
            return
        plan, cursor = recovered
        completed_ids = self._wal.get_completed_step_ids(plan.plan_id)
        modal = RecoverModal(
            plan_id=plan.plan_id,
            completed=len(completed_ids),
            total=len(plan.steps) or len(completed_ids),
        )
        self.push_screen(modal, callback=lambda resume: self._on_recovery_choice(resume, plan))

    def _on_recovery_choice(self, resume: bool, plan: Plan) -> None:
        """Handle the user's choice from the RecoverModal.

        On resume: set ``_current_plan`` and emit PlanStarted through
        the channel. The walker auto-skips already-checkpointed steps
        (per Task 22 / WAL contract). On discard: do nothing (the WAL
        is intentionally left intact so the user can also manually
        delete it).
        """
        if not resume:
            return
        self._current_plan = plan
        # Use call_next so the emit happens on the next event-loop tick
        # (after the modal is fully dismissed).
        self.call_next(self._emit_plan_started, plan)

    async def _emit_plan_started(self, plan: Plan) -> None:
        await self.channel.emit(PlanStarted(plan=plan))

    # ------------------------------------------------------- subscriptions

    def subscribe_event(self, event_type: type, callback: Callable[[Any], None]) -> None:
        """Register ``callback(event)`` for events whose type matches ``event_type``.

        Subscribers for a parent class (e.g. ``WalkEvent``) receive every
        WalkEvent; subscribers for a concrete class (e.g. ``PlanStarted``)
        receive only that concrete type. The dispatcher walks each
        registered (type, callback-list) pair and invokes every callback
        whose type matches via ``isinstance``.
        """
        self._event_subscribers[event_type].append(callback)

    def subscribe_command(self, callback: Callable[[Command], None]) -> None:
        """Register ``callback(command)`` for commands dispatched from TUI."""
        self._command_subscribers.append(callback)

    # ----------------------------------------------------------- dispatchers

    def _dispatch_events(self) -> None:
        """Drain every pending event and notify all subscribers.

        One drain loop per channel — guarantees every WalkEvent is
        delivered to every subscriber that registered for its type
        (or a parent type). No race: only this loop calls
        ``channel.try_recv_event``.
        """
        while True:
            event = self.channel.try_recv_event()
            if event is None:
                return
            # Snapshot to allow callbacks to mutate the subscriber dict.
            for event_type, callbacks in list(self._event_subscribers.items()):
                if isinstance(event, event_type):
                    for cb in callbacks:
                        try:
                            cb(event)
                        except Exception:
                            # Subscriber bugs must not stall the dispatcher.
                            pass

    def _drain_commands(self) -> None:
        """Drain every pending command and route it to the runtime.

        CommandKind -> runtime method mapping lives here so the panels
        stay agnostic of AgentRuntime's mutator surface.
        """
        while True:
            cmd = self.channel.try_recv_command()
            if cmd is None:
                return
            self._handle_command(cmd)

    def _handle_command(self, cmd: Command) -> None:
        """Dispatch a single Command to the right runtime mutator."""
        # First: notify any generic subscribers (e.g. the runtime itself
        # could observe all commands; useful for tests + future hooks).
        for cb in list(self._command_subscribers):
            try:
                cb(cmd)
            except Exception:
                pass

        if cmd.kind == CommandKind.APPROVE_PLAN:
            self._approve_plan()
        elif cmd.kind == CommandKind.REJECT_PLAN:
            self._reject_plan()
        elif cmd.kind == CommandKind.EDIT_STEP:
            self._edit_step(cmd.payload)
        elif cmd.kind == CommandKind.INSERT_STEP:
            self._insert_step(cmd.payload)
        elif cmd.kind == CommandKind.REMOVE_STEP:
            self._remove_step(cmd.payload)
        elif cmd.kind == CommandKind.REORDER_STEPS:
            self._reorder_steps(cmd.payload)
        elif cmd.kind == CommandKind.PAUSE:
            self.runtime.pause() if self.runtime else None
        elif cmd.kind == CommandKind.RESUME:
            self.runtime.resume() if self.runtime else None
        elif cmd.kind == CommandKind.ABORT:
            self.runtime.abort(cmd.payload.get("reason", "")) if self.runtime else None
        elif cmd.kind == CommandKind.ANSWER_QUESTION:
            self._answer_question(cmd.payload)

    # --------------------------------------------------------- command impls

    def _approve_plan(self) -> None:
        """Spawn an asyncio task that calls runtime.walk(_current_plan)."""
        if self.runtime is None or self._current_plan is None:
            self.bell()
            return
        # If a walk is already running, don't start a second one.
        if self._walk_task is not None and not self._walk_task.done():
            return
        self._walk_task = asyncio.create_task(self.runtime.walk(self._current_plan))

    def _reject_plan(self) -> None:
        """Reject the current plan — abort the runtime if present."""
        if self.runtime is not None:
            self.runtime.abort("plan rejected")

    def _edit_step(self, payload: dict) -> None:
        if self.runtime is None:
            return
        step_id = payload.get("step_id")
        step = payload.get("step")
        if not step_id or not isinstance(step, dict):
            return
        new_step = _dict_to_planstep(step)
        self.runtime.edit_step(step_id, new_step)
        # Refresh the plan-tree view by re-emitting PlanStarted with the
        # mutated plan (cheap; panels are idempotent).
        if self._current_plan is not None:
            asyncio.create_task(self.channel.emit(PlanStarted(plan=self._current_plan)))

    def _insert_step(self, payload: dict) -> None:
        if self.runtime is None:
            return
        after_id = payload.get("after_step_id")
        step = payload.get("step")
        if not isinstance(step, dict):
            return
        new_step = _dict_to_planstep(step)
        self.runtime.insert_step(after_id, new_step)
        if self._current_plan is not None:
            asyncio.create_task(self.channel.emit(PlanStarted(plan=self._current_plan)))

    def _remove_step(self, payload: dict) -> None:
        if self.runtime is None:
            return
        step_id = payload.get("step_id")
        if not step_id:
            return
        self.runtime.remove_step(step_id)
        if self._current_plan is not None:
            asyncio.create_task(self.channel.emit(PlanStarted(plan=self._current_plan)))

    def _reorder_steps(self, payload: dict) -> None:
        if self.runtime is None:
            return
        ordered_ids = payload.get("ordered_ids", [])
        if not isinstance(ordered_ids, list):
            return
        self.runtime.reorder_steps(ordered_ids)
        if self._current_plan is not None:
            asyncio.create_task(self.channel.emit(PlanStarted(plan=self._current_plan)))

    def _answer_question(self, payload: dict) -> None:
        if self.runtime is None:
            return
        step_id = payload.get("step_id", "")
        answer = payload.get("answer", "")
        self.runtime.answer_question(step_id, answer)

    # --------------------------------------------------------------- actions

    def action_new_task(self) -> None:
        """Open the NewTaskModal. On submit, plan + emit PlanStarted."""

        def _on_submit(task: str) -> None:
            # Schedule the async plan() on the running loop and bridge
            # the resulting Plan back into the synchronous UI flow.
            async def _plan_and_emit() -> None:
                if self.runtime is None:
                    self.bell()
                    return
                plan = await self.runtime.plan(task)
                self._current_plan = plan
                # The walker normally emits PlanStarted; we emit it here
                # so the UI sees the plan before walking begins.
                await self.channel.emit(PlanStarted(plan=plan))

            asyncio.create_task(_plan_and_emit())

        self.push_screen(NewTaskModal(on_submit=_on_submit))


# ----------------------------------------------------------------------- helpers


def _dict_to_planstep(d: dict) -> PlanStep:
    """Rebuild a PlanStep from a serialized dict (as carried in Command payloads)."""
    return PlanStep(
        id=d["id"],
        kind=PlanStepKind(d["kind"]),
        intent=d.get("intent", ""),
        tool=d.get("tool"),
        args=d.get("args", {}) or {},
        success_criteria=d.get("success_criteria", ""),
        on_failure=OnFailure(d.get("on_failure", "ask")),
        timeout_s=d.get("timeout_s", 120),
    )
