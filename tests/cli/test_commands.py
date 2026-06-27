"""Tests for CLI command imports + instantiation."""
from click.testing import CliRunner


def test_run_command_imports():
    from src.cli.commands.run import run as run_cmd
    assert run_cmd is not None
    assert run_cmd.name == "run"


def test_tui_command_imports():
    from src.cli.commands.tui import tui as tui_cmd
    assert tui_cmd is not None
    assert tui_cmd.name == "tui"


def test_session_command_imports():
    from src.cli.commands.session import session as session_cmd
    assert session_cmd is not None
    assert session_cmd.name == "session"


def test_session_list_with_no_wal(tmp_path):
    from src.cli.commands.session import session
    import os
    os.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(session, ["list"])
    assert result.exit_code == 0
    assert "No WAL found" in result.output or "No plans" in result.output
