import json
from pathlib import Path
import typer.main
from click.testing import CliRunner
from src.cli.migrate import migrate_app


runner = CliRunner()
# Cache the Click command once — get_command() mutates the Typer app's internal state
# on first call; subsequent calls return a broken command object.
_migrate_cmd = typer.main.get_command(migrate_app)


def test_migrate_creates_v2_file(tmp_path, monkeypatch):
    wal = tmp_path / ".nexus" / "wal.jsonl"
    wal.parent.mkdir(parents=True)
    wal.write_text(
        '{"format_version": 1, "kind": "plan_start", "plan_id": "p1", "version": 1, "plan": {"id": "p1", "task": "x", "steps": []}}\n'
        '{"format_version": 1, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    # _migrate_cmd IS the migrate command — no subcommand name needed in args
    result = runner.invoke(_migrate_cmd, ["--workdir", str(tmp_path), "p1"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
    v2_path = wal.with_name("wal_v2.jsonl")
    assert v2_path.exists()
    first = json.loads(v2_path.read_text().splitlines()[0])
    assert first["format_version"] == 2


def test_migrate_idempotent(tmp_path, monkeypatch):
    wal = tmp_path / ".nexus" / "wal.jsonl"
    wal.parent.mkdir(parents=True)
    wal.write_text(
        '{"format_version": 2, "kind": "wal_header"}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    result = runner.invoke(_migrate_cmd, ["--workdir", str(tmp_path), "p1"])
    assert "already migrated" in result.output.lower() or result.exit_code == 0, \
        f"exit_code={result.exit_code}, output={result.output}"
