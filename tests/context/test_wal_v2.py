import json
import pytest
from pathlib import Path
from src.agent.plan import Plan, new_plan_id
from src.context.wal import WALManager


def test_new_wal_writes_v2_header(tmp_path):
    wal = WALManager(path=tmp_path / "wal.jsonl")
    wal.initialize()
    first_line = wal.path.read_text().splitlines()[0]
    rec = json.loads(first_line)
    assert rec["kind"] == "wal_header"
    assert rec["format_version"] == 2


def test_v1_wal_loads_in_v2_reader(tmp_path):
    v1_wal = tmp_path / "wal.jsonl"
    v1_wal.write_text(
        '{"kind": "plan_start", "plan_id": "p1", "version": 1}\n'
        '{"kind": "step_complete", "plan_id": "p1", "cursor": "s1", "result": {"status": "completed"}}\n'
    )
    wal = WALManager(path=v1_wal)
    records = list(wal.iter_records())
    assert len(records) == 2
    assert records[0]["plan_id"] == "p1"


@pytest.mark.asyncio
async def test_checkpoint_with_metadata_writes_metadata_field(tmp_path):
    wal = WALManager(path=tmp_path / "wal.jsonl")
    wal.initialize()
    plan = Plan(plan_id="p1", spec="t", version=1)
    await wal.checkpoint(
        plan=plan, cursor="s1", result={"status": "completed"},
        metadata={"subplan_result": {"status": "completed"}},
    )
    last_line = wal.path.read_text().splitlines()[-1]
    rec = json.loads(last_line)
    assert rec["metadata"]["subplan_result"]["status"] == "completed"
    assert rec["format_version"] == 2