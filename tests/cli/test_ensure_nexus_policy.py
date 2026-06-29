"""Tests for ensure_nexus_policy helper in src.cli.commands.run."""
from __future__ import annotations

from pathlib import Path

from src.cli.commands.run import ensure_nexus_policy


def test_creates_when_missing(tmp_path: Path):
    """No .nexus/policy.yaml yet → helper writes a starter template."""
    project_root = tmp_path
    ensure_nexus_policy(project_root)
    p = project_root / ".nexus" / "policy.yaml"
    assert p.exists()
    text = p.read_text()
    # The default template references 'Nexus Model Policy' and the v1.2 hints.
    assert "Nexus Model Policy" in text
    assert "defaults" in text
    assert "per_role" in text


def test_does_not_overwrite_existing(tmp_path: Path):
    """If .nexus/policy.yaml already exists, helper must leave it untouched."""
    project_root = tmp_path
    policy_path = project_root / ".nexus" / "policy.yaml"
    policy_path.parent.mkdir(parents=True)
    user_text = "# User-authored, do NOT touch\ndefaults:\n  planner: claude-opus-4-8\n"
    policy_path.write_text(user_text)

    ensure_nexus_policy(project_root)

    assert policy_path.read_text() == user_text


def test_creates_parent_directory(tmp_path: Path):
    """The .nexus dir does not pre-exist → helper must create it."""
    project_root = tmp_path
    assert not (project_root / ".nexus").exists()
    ensure_nexus_policy(project_root)
    assert (project_root / ".nexus").is_dir()
    assert (project_root / ".nexus" / "policy.yaml").is_file()