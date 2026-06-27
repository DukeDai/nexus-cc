"""Tests for ControlChannel bidirectional async channel."""
from __future__ import annotations

import asyncio

import pytest

from src.agent.control import Command, CommandKind, ControlChannel, StepResult


class TestControlChannel:
    @pytest.mark.asyncio
    async def test_emit_recv_event(self):
        """emit an event, recv it back, assert same event."""
        ch = ControlChannel()
        event = {"type": "step_complete", "step_id": "s1"}
        await ch.emit(event)
        received = await ch.recv_event()
        assert received == event

    @pytest.mark.asyncio
    async def test_send_recv_command(self):
        """send a Command, recv it back, assert kind + payload match."""
        ch = ControlChannel()
        cmd = Command(kind=CommandKind.APPROVE_PLAN, payload={"plan_id": "p1"})
        await ch.send_command(cmd)
        received = await ch.recv_command()
        assert received.kind == CommandKind.APPROVE_PLAN
        assert received.payload == {"plan_id": "p1"}

    @pytest.mark.asyncio
    async def test_pause_blocks_wait_if_paused(self):
        """start a task that calls wait_if_paused, call pause(), confirm the task is awaiting, then resume(), confirm task completes."""
        ch = ControlChannel()
        ch.pause()  # pause before starting task
        task = asyncio.create_task(ch.wait_if_paused())
        await asyncio.sleep(0.05)  # let task start and block
        assert not task.done()  # task should still be waiting
        ch.resume()
        await asyncio.wait_for(task, timeout=1.0)  # task should complete
        assert task.done()

    @pytest.mark.asyncio
    async def test_abort_sets_flag(self):
        """call abort("test reason"), confirm is_aborted is True and aborted_reason is "test reason"."""
        ch = ControlChannel()
        ch.abort("test reason")
        assert ch.is_aborted is True
        assert ch.aborted_reason == "test reason"
