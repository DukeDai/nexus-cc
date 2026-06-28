import json
from pathlib import Path
from typer.testing import CliRunner
from src.cli.evolve import app


runner = CliRunner()


def test_evolve_with_no_staged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--auto"])
    assert "No staged changes" in result.stdout


def test_evolve_with_auto_applies_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    staged = tmp_path / ".nexus" / "prompts"
    staged.mkdir(parents=True)
    (staged / "staged.json").write_text(json.dumps({
        "changes": {
            "planner": {
                "name": "planner", "version": 2,
                "system_prompt": "new", "updated_at": "2026-07-01T00:00:00",
                "source_episodes": [], "last_updated_walk_count": 5,
            }
        },
        "rationale": {"planner": "test"},
        "created_at": "2026-07-01T00:00:00",
    }))
    result = runner.invoke(app, ["--auto"])
    assert "Applied planner v2" in result.stdout
    assert not (staged / "staged.json").exists()
