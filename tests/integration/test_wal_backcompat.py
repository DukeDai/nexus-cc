"""Backwards-compat tests: replay v1.1 WAL fixtures through the v1.2 WALManager.

The v1.1 WAL format used records like:
    {"kind": "plan_start",  "plan_id": "...", "version": 1, "plan": {...}}
    {"kind": "step_complete", "plan_id": "...", "cursor": "...", "result": {...}}
    {"kind": "plan_end",    "plan_id": "...", "outcome": "success" | "failed"}

v1.2 WALManager writes `step_complete` records with a `format_version: 2` field.
`iter_records` (and `recover` / `get_completed_step_ids`) must remain tolerant of
v1.1 entries so historical logs can be replayed by a v1.2 runtime.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.context.wal import WALManager


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "wal_v1"


def _load_fixture(name: str) -> list[dict]:
    path = FIXTURES_DIR / name
    assert path.exists(), f"Missing fixture: {path}"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_simple_v1_fixture_replays() -> None:
    """The simple v1.1 fixture (1 step, success) round-trips through iter_records."""
    records = _load_fixture("simple.jsonl")
    assert records, "Fixture should not be empty"
    assert records[0]["kind"] == "plan_start"
    assert records[-1]["kind"] == "plan_end"

    # Replay: write them into a fresh WALManager, read back via iter_records.
    tmp = Path("/tmp/_nexus_bc_simple.jsonl")
    if tmp.exists():
        tmp.unlink()
    wal = WALManager(path=tmp)
    for rec in records:
        tmp.write_text(tmp.read_text() + json.dumps(rec) + "\n") if tmp.exists() else tmp.write_text(json.dumps(rec) + "\n")

    replayed = list(wal.iter_records())
    assert len(replayed) == 3
    # iter_records tags v1 records with format_version=1 (no upgrade forced)
    assert all(r["format_version"] == 1 for r in replayed)
    assert replayed[0]["plan_id"] == "p_simple"


def test_failed_v1_fixture_replays() -> None:
    """The failed v1.1 fixture (1 step, failed outcome) survives replay."""
    records = _load_fixture("failed.jsonl")
    tmp = Path("/tmp/_nexus_bc_failed.jsonl")
    tmp.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    wal = WALManager(path=tmp)
    replayed = list(wal.iter_records())
    assert len(replayed) == 3
    step_records = [r for r in replayed if r.get("kind") == "step_complete"]
    assert len(step_records) == 1
    assert step_records[0]["result"]["status"] == "failed"


def test_multi_step_v1_fixture_replays() -> None:
    """The multi-step v1.1 fixture (multiple steps, success) survives replay."""
    records = _load_fixture("multi_step.jsonl")
    tmp = Path("/tmp/_nexus_bc_multi.jsonl")
    tmp.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    wal = WALManager(path=tmp)
    replayed = list(wal.iter_records())
    step_records = [r for r in replayed if r.get("kind") == "step_complete"]
    # The fixture has 2 completed tool steps (s1, s2); s3 is a verify step that
    # never emitted step_complete in v1.1 — that's faithful to the fixture.
    cursors = {r["cursor"] for r in step_records}
    assert cursors == {"s1", "s2"}
    # The trailing plan_end record should still be present.
    assert replayed[-1]["kind"] == "plan_end"
    assert replayed[-1]["outcome"] == "success"


def test_v1_fixture_step_completion_recoverable() -> None:
    """get_completed_step_ids() must pick up v1 step_complete entries by plan_id."""
    records = _load_fixture("multi_step.jsonl")
    tmp = Path("/tmp/_nexus_bc_recover.jsonl")
    tmp.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    wal = WALManager(path=tmp)
    completed = wal.get_completed_step_ids("p_multi")
    # The fixture has 2 step_complete entries for p_multi (s1, s2).
    assert completed == {"s1", "s2"}
    # Unknown plan_id returns empty set (no leakage)
    assert wal.get_completed_step_ids("p_unknown") == set()


def test_mixed_v1_v2_wal_is_readable() -> None:
    """A WAL containing both v1 and v2 records must iterate without error.

    v1 records lack `format_version`; v2 records carry `format_version: 2`.
    iter_records() should tag v1 entries as format_version=1 and pass v2
    entries through unchanged.
    """
    tmp = Path("/tmp/_nexus_bc_mixed.jsonl")
    lines = [
        json.dumps({"kind": "plan_start", "plan_id": "p_mix", "version": 1}),
        json.dumps({"kind": "step_complete", "plan_id": "p_mix", "cursor": "s1", "result": {"status": "completed"}}),
        json.dumps({
            "format_version": 2,
            "kind": "step_complete",
            "plan_id": "p_mix",
            "version": 2,
            "cursor": "s2",
            "result": {"status": "completed"},
        }),
    ]
    tmp.write_text("\n".join(lines) + "\n")

    wal = WALManager(path=tmp)
    replayed = list(wal.iter_records())
    assert len(replayed) == 3
    versions = [r["format_version"] for r in replayed]
    assert versions == [1, 1, 2]

    completed = wal.get_completed_step_ids("p_mix")
    assert completed == {"s1", "s2"}
