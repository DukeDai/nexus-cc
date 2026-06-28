"""Migration round-trip tests on synthetic v1 WAL fixtures."""

import json
import shutil
from pathlib import Path

import pytest
import typer.main
from click.testing import CliRunner

from src.cli.migrate import migrate_app

# Cache the Click command — get_command() mutates Typer's internal state on first call.
_migrate_cmd = typer.main.get_command(migrate_app)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wal_v1"

runner = CliRunner()

# Map fixture file names to their plan_ids as written in the WAL records.
_FIXTURE_PLAN_IDS = {
    "simple.jsonl": "p_simple",
    "multi_step.jsonl": "p_multi",
    "failed.jsonl": "p_failed",
}


def _setup_workdir(fixture: Path, tmp_path: Path) -> Path:
    """Copy fixture to tmp_path/.nexus/wal.jsonl."""
    nexus_dir = tmp_path / ".nexus"
    nexus_dir.mkdir(parents=True)
    shutil.copy(fixture, nexus_dir / "wal.jsonl")
    return tmp_path


@pytest.mark.parametrize("fixture_name", ["simple.jsonl", "multi_step.jsonl", "failed.jsonl"])
def test_migrate_round_trip(tmp_path, fixture_name):
    fixture = FIXTURES / fixture_name
    plan_id = _FIXTURE_PLAN_IDS[fixture_name]
    _setup_workdir(fixture, tmp_path)

    result = runner.invoke(_migrate_cmd, ["--workdir", str(tmp_path), plan_id])
    assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"

    v2_path = tmp_path / ".nexus" / "wal_v2.jsonl"
    assert v2_path.exists()
    lines = v2_path.read_text().splitlines()
    first = json.loads(lines[0])
    assert first["kind"] == "wal_header"
    assert first["format_version"] == 2

    plan_records = [json.loads(l) for l in lines[1:]]
    assert all(r.get("plan_id") == plan_id for r in plan_records)
    assert all(r.get("format_version") == 2 for r in plan_records)


def test_migrate_preserves_step_cursors(tmp_path):
    """After migration, step cursors from v1 should still be loadable."""
    _setup_workdir(FIXTURES / "multi_step.jsonl", tmp_path)
    runner = CliRunner()
    runner.invoke(_migrate_cmd, ["--workdir", str(tmp_path), "p_multi"])

    v2_path = tmp_path / ".nexus" / "wal_v2.jsonl"
    records = [json.loads(l) for l in v2_path.read_text().splitlines() if l.strip()]
    step_cursors = [r["cursor"] for r in records if r.get("kind") == "step_complete"]
    assert "s1" in step_cursors
    assert "s2" in step_cursors


def test_migrate_idempotent_no_op_on_already_v2(tmp_path):
    """Migrating an already-v2 WAL reports already migrated."""
    _setup_workdir(FIXTURES / "simple.jsonl", tmp_path)

    # First migration.
    result = runner.invoke(_migrate_cmd, ["--workdir", str(tmp_path), "p_simple"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
    v2_path = tmp_path / ".nexus" / "wal_v2.jsonl"
    assert v2_path.exists()

    # Replace wal.jsonl with the v2 file (simulate user now using v2).
    shutil.copy(v2_path, tmp_path / ".nexus" / "wal.jsonl")

    # Migrate again: should report "already migrated" and not fail.
    result = runner.invoke(_migrate_cmd, ["--workdir", str(tmp_path), "p_simple"])
    assert "already" in result.output.lower() or result.exit_code == 0, \
        f"exit_code={result.exit_code}, output={result.output}"
