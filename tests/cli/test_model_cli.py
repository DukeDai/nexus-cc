"""Tests for src.cli.commands.model — list / show / resolve."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from src.cli.commands.model import model
from src.llm.model_policy import DEFAULT_POLICY


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch) -> Path:
    """Run from a fresh tmp dir so we see only defaults."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_shows_default_models(runner: CliRunner, project_root: Path):
    result = runner.invoke(model, ["list"])
    assert result.exit_code == 0, result.output
    # Every default model from DEFAULT_POLICY appears at least once.
    seen_models = {DEFAULT_POLICY[h] for h in DEFAULT_POLICY}
    for m in seen_models:
        assert m in result.output, f"missing {m}"
    assert "source" in result.output


def test_list_includes_yaml_overrides(runner: CliRunner, project_root: Path):
    yaml_dir = project_root / ".nexus"
    yaml_dir.mkdir()
    (yaml_dir / "policy.yaml").write_text(
        "defaults:\n  planner: claude-opus-4-8\n"
    )
    result = runner.invoke(model, ["list"])
    assert result.exit_code == 0, result.output
    assert "claude-opus-4-8" in result.output
    assert "yaml" in result.output


def test_list_includes_env_override(runner: CliRunner, project_root: Path, monkeypatch):
    monkeypatch.setenv("NEXUS_MODEL_PLANNER", "claude-opus-4-8")
    result = runner.invoke(model, ["list"])
    assert result.exit_code == 0, result.output
    assert "claude-opus-4-8" in result.output
    assert "env" in result.output


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_default_model(runner: CliRunner, project_root: Path):
    result = runner.invoke(model, ["show", "claude-sonnet-4-6"])
    assert result.exit_code == 0, result.output
    assert "claude-sonnet-4-6" in result.output
    assert "Resolution precedence" in result.output
    # Sonnet is default for planner + critique + verifier_review + evolver
    assert "hints:" in result.output


def test_show_unknown_model(runner: CliRunner, project_root: Path):
    result = runner.invoke(model, ["show", "totally-unknown-xyz"])
    assert result.exit_code == 0, result.output
    assert "Model: totally-unknown-xyz" in result.output
    assert "unknown" in result.output.lower()


def test_show_alias_only_model(runner: CliRunner, project_root: Path):
    """A model in DEFAULT_MODELS but not active in DEFAULT_POLICY appears as alias."""
    result = runner.invoke(model, ["show", "claude-opus-4-8"])
    assert result.exit_code == 0, result.output
    assert "alias" in result.output


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_known_hint(runner: CliRunner, project_root: Path):
    result = runner.invoke(model, ["resolve", "planner"])
    assert result.exit_code == 0, result.output
    assert "Resolved model: claude-sonnet-4-6" in result.output
    assert "Resolution chain" in result.output


def test_resolve_unknown_hint_exits_2(runner: CliRunner, project_root: Path):
    result = runner.invoke(model, ["resolve", "totally_not_a_hint"])
    assert result.exit_code == 2
    assert "Unknown hint" in result.output


def test_resolve_verifier_security_uses_haiku(runner: CliRunner, project_root: Path):
    """v1.2 decision: VERIFIER_SECURITY defaults to claude-haiku-4-5."""
    result = runner.invoke(model, ["resolve", "verifier_security"])
    assert result.exit_code == 0, result.output
    assert "claude-haiku-4-5" in result.output


def test_help_renders(runner: CliRunner):
    result = runner.invoke(model, ["--help"])
    assert result.exit_code == 0
    assert "Model Policy" in result.output