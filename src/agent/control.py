"""ControlChannel - bidirectional async channel between TUI and AgentRuntime."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CommandKind(str, Enum):
    APPROVE_PLAN = "approve_plan"
    REJECT_PLAN = "reject_plan"
    EDIT_STEP = "edit_step"
    INSERT_STEP = "insert_step"
    REMOVE_STEP = "remove_step"
    REORDER_STEPS = "reorder_steps"
    PAUSE = "pause"
    RESUME = "resume"
    ABORT = "abort"
    ANSWER_QUESTION = "answer_question"


@dataclass
class Command:
    kind: CommandKind
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    step_id: str
    status: str
    output: Any = None
    error: str | None = None
    feedback: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ControlChannel:
    def __init__(self, *, max_queue: int = 1000) -> None:
        self._events: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_queue)
        self._commands: asyncio.Queue[Command] = asyncio.Queue(maxsize=max_queue)
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._aborted: bool = False
        self._abort_reason: str = ""

    async def emit(self, event: Any) -> None:
        await self._events.put(event)

    async def recv_event(self) -> Any:
        return await self._events.get()

    def try_recv_event(self) -> Any | None:
        try:
            return self._events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def send_command(self, cmd: Command) -> None:
        await self._commands.put(cmd)

    async def recv_command(self) -> Command:
        return await self._commands.get()

    def try_recv_command(self) -> Command | None:
        try:
            return self._commands.get_nowait()
        except asyncio.QueueEmpty:
            return None

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    async def wait_if_paused(self) -> None:
        await self._pause_event.wait()

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    @property
    def aborted_reason(self) -> str:
        return self._abort_reason

    def abort(self, reason: str = "") -> None:
        self._aborted = True
        self._abort_reason = reason
        self._pause_event.set()
