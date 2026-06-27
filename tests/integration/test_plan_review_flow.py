"""Integration test for the plan-first TUI -> runtime flow.

Exercises the full pipeline:
  1. User opens NewTaskModal (via "n" binding) and submits a task string.
  2. NexusApp calls ``runtime.plan(task)``, captures the resulting Plan,
     and emits PlanStarted through the shared ControlChannel.
  3. A single dispatcher in NexusApp drains the event queue and fans it
     out to subscribed panels (no per-panel set_interval = no race).
  4. PlanPanel's Tree reflects the steps; ExecutionPanel logs the event.
  5. Approving the plan (via "a") puts APPROVE_PLAN on the channel,
     which NexusApp drains and forwards to ``runtime.walk``.

Uses a FakeRuntime that records calls and emits the canonical event
sequence (PlanStarted -> StepStarted -> ToolCallStarted -> ToolCallCompleted
-> StepCompleted -> PlanCompleted) through the channel.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.agent.control import Command, CommandKind, ControlChannel
from src.agent.events import (
    PlanCompleted,
    PlanStarted,
    StepCompleted,
    StepStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from src.agent.plan import (
    OnFailure,
    Plan,
    PlanStep,
    PlanStepKind,
)
from src.tui.app import NexusApp
from src.tui.new_task_modal import NewTaskModal


# ---------------------------------------------------------------------------
# FakeRuntime — replaces real AgentRuntime for the integration test.
# ---------------------------------------------------------------------------


@dataclass
class FakeRuntime:
    """Records plan/walk/edit calls and emits events through the channel.

    Mirrors enough of the AgentRuntime surface for NexusApp's bindings
    and dispatcher to exercise the full plan-review flow without needing
    an LLM, planner, walker, or WAL.
    """

    channel: ControlChannel
    plan_calls: list[str] = field(default_factory=list)
    walk_calls: list[str] = field(default_factory=list)
    edit_calls: list[tuple[str, PlanStep]] = field(default_factory=list)
    insert_calls: list[tuple[str | None, PlanStep]] = field(default_factory=list)
    remove_calls: list[str] = field(default_factory=list)
    reorder_calls: list[list[str]] = field(default_factory=list)
    answer_calls: list[tuple[str, str]] = field(default_factory=list)

    async def plan(self, task: str, *, spec: str | None = None) -> Plan:
        self.plan_calls.append(task)
        return _make_plan(task)

    async def walk(self, plan: Plan | None = None) -> list[Any]:
        target = plan or _make_plan("(none)")
        self.walk_calls.append(target.plan_id)
        # Emit the canonical happy-path event sequence.
        await self.channel.emit(PlanStarted(plan=target))
        for i, step in enumerate(target.steps):
            await self.channel.emit(StepStarted(step=step, index=i, total=len(target.steps)))
            await self.channel.emit(
                ToolCallStarted(tool=step.tool or "Bash", args=step.args, step_id=step.id)
            )
            await self.channel.emit(ToolCallCompleted(result="ok", step_id=step.id))
            await self.channel.emit(StepCompleted(step=step, result="ok"))
        await self.channel.emit(PlanCompleted(results=[]))
        return []

    def edit_step(self, step_id: str, new_step: PlanStep) -> None:
        self.edit_calls.append((step_id, new_step))

    def insert_step(self, after_id: str | None, new_step: PlanStep) -> None:
        self.insert_calls.append((after_id, new_step))

    def remove_step(self, step_id: str) -> None:
        self.remove_calls.append(step_id)

    def reorder_steps(self, ordered_ids: list[str]) -> None:
        self.reorder_calls.append(list(ordered_ids))

    def answer_question(self, step_id: str, answer: str) -> None:
        self.answer_calls.append((step_id, answer))

    def pause(self) -> None:
        self.channel.pause()

    def resume(self) -> None:
        self.channel.resume()

    def abort(self, reason: str = "") -> None:
        self.channel.abort(reason)


def _make_plan(task: str) -> Plan:
    """Build a deterministic 2-step Plan for tests."""
    steps = [
        PlanStep(
            id="step_a",
            kind=PlanStepKind.TOOL,
            intent=f"first: {task}",
            tool="Read",
            args={"path": "/tmp/a"},
            success_criteria="ok",
            on_failure=OnFailure.ASK,
            timeout_s=60,
        ),
        PlanStep(
            id="step_b",
            kind=PlanStepKind.TOOL,
            intent=f"second: {task}",
            tool="Write",
            args={"path": "/tmp/b"},
            success_criteria="ok",
            on_failure=OnFailure.ASK,
            timeout_s=60,
        ),
    ]
    return Plan(plan_id=f"plan_{abs(hash(task)) & 0xFFFFFFFF:08x}", spec=task, steps=steps)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_task_modal_emits_plan_started_event():
    """Submitting the NewTaskModal triggers runtime.plan() and emits PlanStarted."""
    channel = ControlChannel()
    runtime = FakeRuntime(channel=channel)
    app = NexusApp(channel=channel, runtime=runtime)

    async with app.run_test() as pilot:
        # Drainer is active; open and submit the modal.
        app.action_new_task()
        await pilot.pause()

        modal = app.screen
        assert isinstance(modal, NewTaskModal)

        modal_input = modal.query_one("#task-input")
        modal_input.value = "test task"
        await pilot.press("enter")
        await pilot.pause()

        # runtime.plan() was called with our task string.
        assert runtime.plan_calls == ["test task"]

        # PlanStarted event was emitted through the channel — let the
        # dispatcher pick it up and the panels render.
        for _ in range(40):
            await pilot.pause()
            if app._current_plan is not None and app._current_plan.spec == "test task":
                break
        assert app._current_plan is not None
        assert app._current_plan.spec == "test task"
        assert len(app._current_plan.steps) == 2

        # PlanPanel tree should show both step leaves.
        plan_panel = app.query_one("#plan-pane")
        for _ in range(40):
            await pilot.pause()
            if len(plan_panel.tree.root.children) == 2:
                break
        assert len(plan_panel.tree.root.children) == 2

        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_approve_plan_command_triggers_runtime_walk():
    """Pressing 'a' enqueues APPROVE_PLAN; the drainer forwards it to runtime.walk()."""
    channel = ControlChannel()
    runtime = FakeRuntime(channel=channel)
    app = NexusApp(channel=channel, runtime=runtime)

    async with app.run_test() as pilot:
        # Set a current plan so APPROVE_PLAN knows what to walk.
        app._current_plan = _make_plan("approve-me")

        # Approve via key binding. PlanPanel owns the 'a' binding; focus it
        # so the binding actually fires.
        plan_panel = app.query_one("#plan-pane")
        plan_panel.focus()
        await pilot.press("a")
        await pilot.pause()

        # Wait for walk() to complete and emit PlanCompleted.
        for _ in range(80):
            await pilot.pause()
            if runtime.walk_calls and runtime.walk_calls[0] == app._current_plan.plan_id:
                # Confirm PlanCompleted reached the dispatcher by checking
                # the execution panel log captured "Plan complete".
                exec_panel = app.query_one("#execution-pane")
                log_text = "\n".join(
                    line.plain if hasattr(line, "plain") else str(line)
                    for line in exec_panel.exec_log.lines
                )
                if "Plan complete" in log_text:
                    break

        assert runtime.walk_calls, "runtime.walk was never called"
        assert runtime.walk_calls[0] == app._current_plan.plan_id

        # All step-completion markers propagated to the tree.
        for _ in range(40):
            await pilot.pause()
            labels = [str(c.label) for c in plan_panel.tree.root.children]
            if all("✓" in lab for lab in labels):
                break
        labels = [str(c.label) for c in plan_panel.tree.root.children]
        assert all("✓" in lab for lab in labels), labels

        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_dispatcher_fans_event_out_to_multiple_panels():
    """A single dispatcher delivers one event to all subscribed panels — no race.

    With the old per-panel set_interval design, each panel would race to
    try_recv_event and the loser would silently drop events. Now there is
    exactly one drain loop (NexusApp._dispatch_events) that notifies every
    subscriber.
    """
    channel = ControlChannel()
    runtime = FakeRuntime(channel=channel)
    app = NexusApp(channel=channel, runtime=runtime)

    async with app.run_test() as pilot:
        # Inject a PlanStarted and let the dispatcher fire once.
        plan = _make_plan("fanout")
        await channel.emit(PlanStarted(plan=plan))

        # Both panels should observe the event via the dispatcher.
        plan_panel = app.query_one("#plan-pane")
        exec_panel = app.query_one("#execution-pane")

        for _ in range(40):
            await pilot.pause()
            tree_ok = len(plan_panel.tree.root.children) == 2
            log_text = "\n".join(
                line.plain if hasattr(line, "plain") else str(line)
                for line in exec_panel.exec_log.lines
            )
            log_ok = "Plan started" in log_text
            if tree_ok and log_ok:
                break

        assert len(plan_panel.tree.root.children) == 2
        log_text = "\n".join(
            line.plain if hasattr(line, "plain") else str(line)
            for line in exec_panel.exec_log.lines
        )
        assert "Plan started" in log_text

        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_edit_step_command_rebuilds_planstep_and_calls_runtime():
    """EDIT_STEP on the channel is drained and forwarded to runtime.edit_step."""
    channel = ControlChannel()
    runtime = FakeRuntime(channel=channel)
    app = NexusApp(channel=channel, runtime=runtime)

    async with app.run_test() as pilot:
        # Set up plan + tree so the panel can resolve the step id.
        plan = _make_plan("edit-me")
        app._current_plan = plan
        await channel.emit(PlanStarted(plan=plan))
        for _ in range(40):
            await pilot.pause()
            if app.query_one("#plan-pane").tree.root.children:
                break

        new_step = PlanStep(
            id="step_a",
            kind=PlanStepKind.TOOL,
            intent="edited intent",
            tool="Read",
            args={"path": "/tmp/new"},
            success_criteria="ok",
            on_failure=OnFailure.ASK,
            timeout_s=60,
        )

        # Put the command on the channel as the panel would.
        await channel.send_command(
            Command(
                CommandKind.EDIT_STEP,
                payload={"step_id": "step_a", "step": _planstep_to_dict(new_step)},
            )
        )

        for _ in range(40):
            await pilot.pause()
            if runtime.edit_calls:
                break

        assert len(runtime.edit_calls) == 1
        step_id, returned = runtime.edit_calls[0]
        assert step_id == "step_a"
        assert returned.intent == "edited intent"
        assert returned.args == {"path": "/tmp/new"}

        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_remove_step_command_calls_runtime():
    """REMOVE_STEP on the channel is drained and forwarded to runtime.remove_step."""
    channel = ControlChannel()
    runtime = FakeRuntime(channel=channel)
    app = NexusApp(channel=channel, runtime=runtime)

    async with app.run_test() as pilot:
        plan = _make_plan("remove-me")
        app._current_plan = plan
        await channel.emit(PlanStarted(plan=plan))
        for _ in range(40):
            await pilot.pause()
            if app.query_one("#plan-pane").tree.root.children:
                break

        await channel.send_command(
            Command(CommandKind.REMOVE_STEP, payload={"step_id": "step_b"})
        )

        for _ in range(40):
            await pilot.pause()
            if runtime.remove_calls:
                break

        assert runtime.remove_calls == ["step_b"]
        await pilot.press("ctrl+c")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _planstep_to_dict(step: PlanStep) -> dict[str, Any]:
    """Serialize a PlanStep into the payload dict NexusApp expects."""
    return {
        "id": step.id,
        "kind": step.kind.value,
        "intent": step.intent,
        "tool": step.tool,
        "args": step.args,
        "success_criteria": step.success_criteria,
        "on_failure": step.on_failure.value,
        "timeout_s": step.timeout_s,
    }
