from pathlib import Path
from typer.testing import CliRunner
from src.cli.prompt import app


runner = CliRunner()


def test_prompt_list_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No prompt templates registered" in result.stdout


def test_prompt_show_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["show", "planner"])
    assert "not found" in result.stdout.lower() or result.exit_code != 0