"""WALManager - step-level JSONL checkpoint + recovery for plan-first Nexus."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..agent.plan import Plan


class WALManager:
    """JSONL append-only checkpoint log keyed by plan_id.

    Each checkpoint is one JSON line: {tx: "checkpoint", plan_id, version, cursor, result}.
    Recover() returns the most recent checkpoint for any plan; get_completed_step_ids returns the set of step_ids already checkpointed.
    """

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()

    async def checkpoint(self, *, plan: Plan, cursor: str, result: dict[str, Any] | None = None) -> None:
        entry = {
            "tx": "checkpoint",
            "plan_id": plan.plan_id,
            "version": plan.version,
            "cursor": cursor,
            "result": result or {},
        }
        async with self._lock:
            with self._path.open("a") as f:
                f.write(json.dumps(entry) + "\n")

    async def recover(self) -> tuple[Plan, str] | None:
        if not self._path.exists():
            return None
        last_plan: Plan | None = None
        last_cursor: str | None = None
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("tx") != "checkpoint":
                    continue
                # Rebuild a minimal Plan (no step data needed for cursor recovery)
                last_plan = Plan(
                    plan_id=entry["plan_id"],
                    spec="",
                    steps=[],
                    assumptions=[],
                    risks=[],
                )
                last_plan.version = entry.get("version", 1)
                last_cursor = entry["cursor"]
        return (last_plan, last_cursor) if last_plan and last_cursor else None

    def get_completed_step_ids(self, plan_id: str) -> set[str]:
        completed: set[str] = set()
        if not self._path.exists():
            return completed
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("tx") == "checkpoint" and entry.get("plan_id") == plan_id:
                    completed.add(entry["cursor"])
        return completed
