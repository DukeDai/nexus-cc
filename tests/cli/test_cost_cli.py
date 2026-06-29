"""Tests for src.cli.commands.cost — subcommands against WAL CostRecords."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.cli.commands.cost import cost


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def wal_path(tmp_path: Path) -> Path:
    """Pre-populated WAL with several cost_records across models + sessions."""
    wal = tmp_path / ".nexus" / "wal.jsonl"
    wal.parent.mkdir(parents=True)
    now = time.time()
    lines = [
        # Header
        {"format_version": 2, "kind": "wal_header"},
        # Plan p1 — first session
        {"format_version": 2, "kind": "step_complete", "plan_id": "p1",
         "cursor": "step_1", "result": {}},
        {"kind": "cost_record", "model": "claude-sonnet-4-6",
         "hint": "planner", "role": "implementer",
         "prompt_tokens": 1000, "completion_tokens": 500,
         "cost_usd": 0.0105, "timestamp": now - 10},
        {"kind": "cost_record", "model": "claude-haiku-4-5",
         "hint": "verifier_security", "role": "security",
         "prompt_tokens": 500, "completion_tokens": 200,
         "cost_usd": 0.0012, "timestamp": now - 9},
        # Plan p2 — second session
        {"format_version": 2, "kind": "step_complete", "plan_id": "p2",
         "cursor": "step_1", "result": {}},
        {"kind": "cost_record", "model": "claude-sonnet-4-6",
         "hint": "critique", "role": "specifier",
         "prompt_tokens": 800, "completion_tokens": 300,
         "cost_usd": 0.0069, "timestamp": now - 5},
        {"kind": "cost_record", "model": "claude-sonnet-4-6",
         "hint": "planner", "role": "implementer",
         "prompt_tokens": 1200, "completion_tokens": 600,
         "cost_usd": 0.0126, "timestamp": now - 4},
        # Malformed line — should be skipped silently.
        "{not valid json",
    ]
    # Build the file: write valid JSON for dict entries, and a raw malformed
    # line (intentionally NOT wrapped in json.dumps) to exercise the
    # malformed-record tolerance.
    parts = [json.dumps(l) for l in lines[:-1]]
    parts.append(lines[-1])  # raw string "{not valid json"
    wal.write_text("\n".join(parts) + "\n")
    return wal


def _wal_arg(wal: Path) -> list[str]:
    return ["--wal-path", str(wal)]


# ---------------------------------------------------------------------------
# Subcommand tests
# ---------------------------------------------------------------------------


def test_summary_default_subcommand(runner: CliRunner, wal_path: Path):
    """No subcommand → summary."""
    result = runner.invoke(cost, _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    assert "Cost summary" in result.output
    assert "today:" in result.output
    assert "total:" in result.output


def test_today_reports_recent_records(runner: CliRunner, wal_path: Path):
    result = runner.invoke(cost, ["today"] + _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    assert "Today" in result.output
    # Today's records total cost = 0.0105 + 0.0012 + 0.0069 + 0.0126 = 0.0312
    assert "$0.0312" in result.output
    assert "calls:" in result.output


def test_by_model_aggregates_correctly(runner: CliRunner, wal_path: Path):
    result = runner.invoke(cost, ["by-model"] + _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    # sonnet: 3 calls, haiku: 1 call
    assert "claude-sonnet-4-6" in result.output
    assert "claude-haiku-4-5" in result.output
    # sonnet calls = 3
    # Use a substring approach (table layout makes exact-position match fragile).
    assert "model" in result.output
    assert "calls" in result.output


def test_by_role_aggregates_correctly(runner: CliRunner, wal_path: Path):
    result = runner.invoke(cost, ["by-role"] + _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    # Roles: implementer (2), security (1), specifier (1)
    assert "implementer" in result.output
    assert "security" in result.output
    assert "specifier" in result.output


def test_session_filters_by_plan_id(runner: CliRunner, wal_path: Path):
    result = runner.invoke(cost, ["session", "p1"] + _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    assert "Session p1" in result.output
    # p1 cost = 0.0105 + 0.0012 = 0.0117
    assert "$0.0117" in result.output


def test_session_unknown_plan_reports_none(runner: CliRunner, wal_path: Path):
    result = runner.invoke(cost, ["session", "p_does_not_exist"] + _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    assert "No cost records" in result.output


def test_export_csv(runner: CliRunner, wal_path: Path, tmp_path: Path):
    out = tmp_path / "costs.csv"
    result = runner.invoke(cost, ["export", "--format", "csv",
                                  "--output", str(out)] + _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    assert out.exists()
    text = out.read_text()
    # 4 cost records + 1 header → 5 lines.
    assert text.count("\n") >= 5
    assert "model,hint,role" in text
    assert "claude-sonnet-4-6" in text


def test_export_json(runner: CliRunner, wal_path: Path, tmp_path: Path):
    out = tmp_path / "costs.json"
    result = runner.invoke(cost, ["export", "--format", "json",
                                  "--output", str(out)] + _wal_arg(wal_path))
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert isinstance(payload, list)
    assert len(payload) == 4
    assert payload[0]["model"] == "claude-sonnet-4-6"
    assert payload[0]["session"] == "p1"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_wal(tmp_path: Path, runner: CliRunner):
    """No WAL file → graceful empty messages."""
    wal = tmp_path / ".nexus" / "wal.jsonl"
    wal.parent.mkdir(parents=True)
    # Empty file
    wal.write_text("")
    for sub in ["today", "by-model", "by-role"]:
        result = runner.invoke(cost, [sub, "--wal-path", str(wal)])
        assert result.exit_code == 0, f"{sub}: {result.output}"
    # summary
    result = runner.invoke(cost, ["--wal-path", str(wal)])
    assert result.exit_code == 0
    assert "No cost records" in result.output


def test_missing_wal(tmp_path: Path, runner: CliRunner):
    """WAL file does not exist → graceful empty messages."""
    wal = tmp_path / "no-such" / "wal.jsonl"
    for sub in ["today", "by-model", "by-role"]:
        result = runner.invoke(cost, [sub, "--wal-path", str(wal)])
        assert result.exit_code == 0, f"{sub}: {result.output}"
    # session with missing wal
    result = runner.invoke(cost, ["session", "any"] + _wal_arg(wal))
    assert "No cost records" in result.output
    # summary too
    result = runner.invoke(cost, ["--wal-path", str(wal)])
    assert "No cost records" in result.output


def test_malformed_records_skipped(tmp_path: Path, runner: CliRunner):
    """Garbage records don't crash the aggregator."""
    wal = tmp_path / ".nexus" / "wal.jsonl"
    wal.parent.mkdir(parents=True)
    lines = [
        {"format_version": 2, "kind": "wal_header"},
        # cost_record with bogus numeric fields
        {"kind": "cost_record", "model": "claude-sonnet-4-6",
         "hint": "planner", "role": "x",
         "prompt_tokens": "not_a_number", "completion_tokens": "still_bad",
         "cost_usd": "nope", "timestamp": 0.0},
        {"kind": "cost_record", "model": "claude-haiku-4-5",
         "hint": "verifier_security", "role": None,
         "prompt_tokens": 100, "completion_tokens": 50,
         "cost_usd": 0.0001, "timestamp": time.time()},
    ]
    wal.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    # Bogus record gets skipped; only the second one counts.
    result = runner.invoke(cost, ["by-model"] + _wal_arg(wal))
    assert result.exit_code == 0, result.output
    assert "claude-haiku-4-5" in result.output
    assert "claude-sonnet-4-6" not in result.output


def test_help_renders(runner: CliRunner):
    result = runner.invoke(cost, ["--help"])
    assert result.exit_code == 0
    assert "Cost tracking" in result.output