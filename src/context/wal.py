"""WALManager - step-level JSONL checkpoint + recovery for plan-first Nexus."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent.plan import Plan


WAL_FORMAT_VERSION = 2


class WALManager:
    """JSONL append-only checkpoint log keyed by plan_id.

    Each checkpoint is one JSON line: {tx: "checkpoint", plan_id, version, cursor, result}.
    Recover() returns the most recent checkpoint for any plan; get_completed_step_ids returns the set of step_ids already checkpointed.
    """

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def initialize(self) -> None:
        """Create WAL file with v2 header if it doesn't exist."""
        if self._path.exists():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w") as f:
            f.write(json.dumps({
                "format_version": WAL_FORMAT_VERSION,
                "kind": "wal_header",
                "created_at": datetime.now().isoformat(),
                "nexus_version": "1.1.0",
            }) + "\n")

    async def checkpoint(
        self,
        *,
        plan: Plan,
        cursor: str,
        result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Async checkpoint using a Plan object (v1.1 canonical API).

        Accepts a Plan directly so callers don't have to thread plan_id/version
        through every step. Optional metadata block lets callers tag sub-plan
        results, verifier outcomes, etc., per WAL v2 schema.
        """
        record = {
            "format_version": WAL_FORMAT_VERSION,
            "kind": "step_complete",
            "plan_id": plan.plan_id,
            "version": plan.version,
            "cursor": cursor,
            "result": result or {},
        }
        if metadata is not None:
            record["metadata"] = metadata
        async with self._lock:
            with self._path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")

    # Backwards-compat alias — v1.0 callers used this name.
    checkpoint_async = checkpoint

    def iter_records(self):
        """Yield each JSON record in the WAL. v1 and v2 records both supported."""
        if not self._path.exists():
            return
        for line in self._path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            # v1 records lack format_version; treat them as version 1.
            if "format_version" not in rec:
                rec["format_version"] = 1
            yield rec

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
                if entry.get("tx") != "checkpoint" and entry.get("kind") != "step_complete":
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
                if (entry.get("tx") == "checkpoint" or entry.get("kind") == "step_complete") and entry.get("plan_id") == plan_id:
                    completed.add(entry["cursor"])
        return completed