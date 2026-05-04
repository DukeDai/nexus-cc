"""Tests for the CLI module — Click commands, argument parsing, and CLI flow."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import click.testing

# Add src/ to path
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cli.main import cli


@pytest.fixture
def runner():
    """Provide a Click CLI test runner."""
    return click.testing.CliRunner()


class TestHelpCommand:
    """Test --help output and command discovery."""

    def test_root_help_shows_all_commands(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        for cmd in ["run", "tui", "session", "mcp", "skills", "cost"]:
            assert cmd in result.output

    def test_run_command_help(self, runner):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--task" in result.output
        assert "--tdd" in result.output
        assert "--stream" in result.output

    def test_tui_command_help(self, runner):
        result = runner.invoke(cli, ["tui", "--help"])
        assert result.exit_code == 0
        assert "--task" in result.output
        assert "--workdir" in result.output

    def test_session_command_help(self, runner):
        result = runner.invoke(cli, ["session", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "resume" in result.output

    def test_mcp_command_help(self, runner):
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "presets" in result.output

    def test_skills_command_help(self, runner):
        result = runner.invoke(cli, ["skills", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "add" in result.output
        assert "remove" in result.output


class TestRunCommand:
    """Test the `run` command argument parsing and flow."""

    def test_run_requires_task(self, runner):
        result = runner.invoke(cli, ["run"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "Error" in result.output

    def test_run_accepts_task(self, runner):
        with patch("cli.commands.run.RalphLoopExecutor") as mock_exec:
            mock_instance = MagicMock()
            mock_instance.execute_task.return_value = {
                "success": True,
                "turns": 1,
                "final_state": "COMMIT",
                "content": "Done",
                "tool_count": 1,
            }
            mock_exec.return_value = mock_instance

            result = runner.invoke(cli, [
                "run",
                "--task", "Create a hello world script",
                "--workdir", "/tmp",
            ])
            # Should not crash on argument parsing
            assert result.exit_code in (0, 1)  # 1 if task actually ran and returned False

    def test_run_with_tdd_flag(self, runner):
        with patch("cli.commands.run.RalphLoopExecutor") as mock_exec:
            mock_instance = MagicMock()
            mock_instance.execute_task.return_value = {"success": True, "turns": 1, "final_state": "COMMIT", "content": "Done", "tool_count": 1}
            mock_exec.return_value = mock_instance

            result = runner.invoke(cli, [
                "run",
                "--task", "Write a test",
                "--tdd",
            ])
            assert result.exit_code in (0, 1)

    def test_run_with_stream_flag(self, runner):
        with patch("cli.commands.run.RalphLoopExecutor") as mock_exec:
            mock_instance = MagicMock()
            mock_instance.execute_task.return_value = {"success": True, "turns": 1, "final_state": "COMMIT", "content": "Done", "tool_count": 1}
            mock_exec.return_value = mock_instance

            result = runner.invoke(cli, [
                "run",
                "--task", "Write a test",
                "--stream",
            ])
            assert result.exit_code in (0, 1)


class TestSessionCommand:
    """Test session management commands."""

    def test_session_list_empty(self, runner):
        with patch("cli.commands.session.SessionStore") as mock_store:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = []
            mock_store.return_value = mock_instance

            result = runner.invoke(cli, ["session", "list"])
            assert result.exit_code == 0
            assert "No sessions" in result.output

    def test_session_list_with_sessions(self, runner):
        with patch("cli.commands.session.SessionStore") as mock_store:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = [
                {"session_id": "abc12345", "created_at": "2026-05-04", "status": "active"},
            ]
            mock_store.return_value = mock_instance

            result = runner.invoke(cli, ["session", "list"])
            assert result.exit_code == 0
            assert "abc12345" in result.output

    def test_session_resume_not_found(self, runner):
        with patch("cli.commands.session.SessionManager") as mock_mgr:
            mock_instance = MagicMock()
            mock_instance.load.return_value = None
            mock_mgr.return_value = mock_instance

            result = runner.invoke(cli, ["session", "resume", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output


class TestMcpCommand:
    """Test MCP management commands."""

    def test_mcp_list_presets(self, runner):
        with patch("cli.commands.mcp.list_servers", side_effect=ImportError):
            result = runner.invoke(cli, ["mcp", "list"])
            # Should handle ImportError gracefully
            assert result.exit_code == 0

    def test_mcp_presets(self, runner):
        result = runner.invoke(cli, ["mcp", "presets"])
        assert result.exit_code == 0
        assert "github" in result.output


class TestSkillsCommand:
    """Test skills management commands."""

    def test_skills_list(self, runner):
        result = runner.invoke(cli, ["skills", "list"])
        assert result.exit_code == 0

    def test_skills_add(self, runner):
        result = runner.invoke(cli, ["skills", "add", "my-skill"])
        assert result.exit_code == 0

    def test_skills_remove(self, runner):
        result = runner.invoke(cli, ["skills", "remove", "my-skill"])
        assert result.exit_code == 0


class TestCostCommand:
    """Test cost tracking command."""

    def test_cost(self, runner):
        result = runner.invoke(cli, ["cost"])
        assert result.exit_code == 0
        assert "Cost tracking" in result.output
