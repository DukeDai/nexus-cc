"""Tests for WALManager - step-level JSONL checkpoint + recovery."""
from __future__ import annotations

import pytest

from src.agent.plan import Plan, new_plan_id, new_step_id
from src.context.wal import WALManager


@pytest.mark.asyncio
async def test_checkpoint_then_recover_returns_plan_and_cursor(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WALManager(path=wal_path)
    plan = Plan(plan_id=new_plan_id(), spec="test")
    await wal.checkpoint(plan=plan, cursor=new_step_id(), result={"output": "ok"})
    await wal.checkpoint(plan=plan, cursor=new_step_id(), result={"output": "ok2"})
    recovered = await wal.recover()
    assert recovered is not None
    rec_plan, rec_cursor = recovered
    assert rec_plan.plan_id == plan.plan_id
    assert rec_cursor  # some step_id string


@pytest.mark.asyncio
async def test_get_completed_step_ids_returns_set(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WALManager(path=wal_path)
    plan = Plan(plan_id=new_plan_id(), spec="test")
    s1, s2, s3 = new_step_id(), new_step_id(), new_step_id()
    await wal.checkpoint(plan=plan, cursor=s1)
    await wal.checkpoint(plan=plan, cursor=s2)
    await wal.checkpoint(plan=plan, cursor=s3)
    completed = wal.get_completed_step_ids(plan.plan_id)
    assert completed == {s1, s2, s3}


@pytest.mark.asyncio
async def test_recover_empty_returns_none(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WALManager(path=wal_path)
    assert await wal.recover() is None
