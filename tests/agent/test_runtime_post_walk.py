"""Tests for AgentRuntime post_walk_hook (evolver integration)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.control import ControlChannel, StepResult
from src.agent.evolution import Evolver
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agent.prompts import PromptTemplateRegistry
from src.agent.runtime import AgentRuntime


@pytest.mark.asyncio
async def test_walk_invokes_evolver_record_outcome(tmp_path):
    evolver = MagicMock(spec=Evolver)
    evolver.record_outcome = MagicMock()
    evolver.update_prompt_registry = MagicMock(return_value=MagicMock(changes={}, rationale={}))

    llm = MagicMock()
    tools = MagicMock()
    tools.execute = AsyncMock(return_value="ok")
    wal = MagicMock()
    wal.checkpoint = AsyncMock()
    wal.get_completed_step_ids = MagicMock(return_value=set())
    channel = ControlChannel()
    channel.wait_if_paused = AsyncMock()
    channel.emit = AsyncMock()
    runtime = AgentRuntime(
        llm=llm,
        tools=tools,
        verification=MagicMock(),
        wal=wal,
        channel=channel,
        evolver=evolver,
        prompt_registry=PromptTemplateRegistry(path=tmp_path / ".nexus" / "prompts"),
        workdir=tmp_path,
    )
    plan = Plan(plan_id="p1", spec="Test plan", steps=[
        PlanStep(id="s1", kind=PlanStepKind.TOOL, tool="Read", intent="Read file"),
    ])
    await runtime.walk(plan)
    evolver.record_outcome.assert_called_once()