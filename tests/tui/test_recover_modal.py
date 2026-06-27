"""Tests for RecoverModal - Textual ModalScreen for resuming a recovered plan."""
from __future__ import annotations

import pytest

from src.agent.control import ControlChannel
from src.tui.app import NexusApp
from src.tui.recover_modal import RecoverModal


@pytest.mark.asyncio
async def test_modal_renders_with_resume_discard_buttons():
    """RecoverModal opens with both Resume and Discard buttons and shows plan info."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)

    async with app.run_test() as pilot:
        modal = RecoverModal(plan_id="plan_abc12345", completed=2, total=5)
        await app.push_screen(modal)
        await pilot.pause()

        # Both buttons are present and queryable (query the modal's own DOM)
        assert modal.query_one("#resume-btn")
        assert modal.query_one("#discard-btn")

        # Plan id is rendered
        plan_id_widget = modal.query_one("#recover-plan-id")
        assert "plan_abc12345" in plan_id_widget.content

        # Progress shows "2/5"
        progress_widget = modal.query_one("#recover-progress")
        assert "2/5" in progress_widget.content

        # Dismiss to keep test cleanup clean.
        modal.dismiss(False)
        await pilot.pause()


@pytest.mark.asyncio
async def test_resume_binding_returns_true():
    """Pressing 'y' dismisses the modal with True (user wants to resume)."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)

    received: list[bool] = []

    async with app.run_test() as pilot:
        modal = RecoverModal(plan_id="plan_xyz", completed=0, total=3)
        await app.push_screen(modal, callback=received.append)
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert received == [True]


@pytest.mark.asyncio
async def test_discard_binding_returns_false():
    """Pressing 'n' dismisses the modal with False (user wants to discard)."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)

    received: list[bool] = []

    async with app.run_test() as pilot:
        modal = RecoverModal(plan_id="plan_xyz", completed=0, total=3)
        await app.push_screen(modal, callback=received.append)
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        assert received == [False]


@pytest.mark.asyncio
async def test_app_offer_recovery_when_wal_has_plan(tmp_path):
    """Startup recovery: WAL with a checkpoint triggers RecoverModal push."""
    import asyncio

    from src.agent.events import PlanStarted
    from src.agent.plan import Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
    from src.context.wal import WALManager

    wal_path = tmp_path / "wal.jsonl"
    wal = WALManager(path=wal_path)

    # Pre-populate WAL with a plan + checkpoint so recover() returns it.
    plan_id = new_plan_id()
    steps = [
        PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="s1", tool="Echo"),
        PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="s2", tool="Echo"),
    ]
    plan = Plan(plan_id=plan_id, spec="test", steps=steps)
    await wal.checkpoint(plan=plan, cursor=steps[0].id)

    channel = ControlChannel()
    app = NexusApp(channel=channel, wal=wal)

    # Subscribe BEFORE running so we capture the emitted PlanStarted.
    received_events: list[PlanStarted] = []
    app.subscribe_event(PlanStarted, received_events.append)

    async with app.run_test() as pilot:
        # on_mount schedules _maybe_offer_recovery via call_after_refresh
        await pilot.pause()
        # The modal should be on the screen now
        await pilot.pause()

        # Walk the stack to find the RecoverModal
        from src.tui.recover_modal import RecoverModal as _RM
        screens = list(app.screen_stack)
        assert any(isinstance(s, _RM) for s in screens), (
            f"Expected RecoverModal on screen stack, got {[type(s).__name__ for s in screens]}"
        )

        # Press 'y' to resume and verify PlanStarted gets emitted.
        rm_screen = next(s for s in screens if isinstance(s, _RM))
        rm_screen.dismiss(True)
        # Allow multiple ticks so the emit and dispatch complete.
        for _ in range(10):
            await pilot.pause()
        await asyncio.sleep(0)

        # Now _current_plan is set on the app
        assert app._current_plan is not None
        assert app._current_plan.plan_id == plan_id

        # And the event was dispatched to subscribers
        plan_started_events = [e for e in received_events if isinstance(e, PlanStarted)]
        assert plan_started_events, f"Expected PlanStarted event, got {received_events}"
        assert plan_started_events[0].plan.plan_id == plan_id


@pytest.mark.asyncio
async def test_app_no_modal_when_wal_is_empty(tmp_path):
    """Startup recovery: empty WAL does NOT push any modal."""
    from src.context.wal import WALManager

    wal = WALManager(path=tmp_path / "wal.jsonl")  # empty
    channel = ControlChannel()
    app = NexusApp(channel=channel, wal=wal)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from src.tui.recover_modal import RecoverModal as _RM
        assert not any(isinstance(s, _RM) for s in app.screen_stack)


@pytest.mark.asyncio
async def test_app_no_modal_when_wal_is_none():
    """Startup recovery: WAL=None does NOT push any modal (no crash)."""
    channel = ControlChannel()
    app = NexusApp(channel=channel, wal=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from src.tui.recover_modal import RecoverModal as _RM
        assert not any(isinstance(s, _RM) for s in app.screen_stack)