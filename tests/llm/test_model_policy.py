"""Tests for src.llm.model_policy."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".nexus").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip NEXUS_MODEL_* env vars so precedence tests aren't polluted."""
    for hint in ModelHint:
        monkeypatch.delenv(f"NEXUS_MODEL_{hint.value.upper()}", raising=False)


def test_default_policy_security_is_haiku():
    """VERIFIER_SECURITY is the only deliberate cost-downgrade → haiku."""
    assert DEFAULT_POLICY[ModelHint.VERIFIER_SECURITY] == "claude-haiku-4-5"
    assert DEFAULT_POLICY[ModelHint.PLANNER] == "claude-sonnet-4-6"


def test_precedence_cli_wins_over_everything(project_root, monkeypatch):
    """cli_override > per_role > env > defaults."""
    yaml_path = project_root / ".nexus" / "policy.yaml"
    yaml_path.write_text("per_role:\n  implementer: claude-opus-4-8\n")
    monkeypatch.setenv("NEXUS_MODEL_PLANNER", "claude-opus-4-8")
    policy = ModelPolicy.load(project_root, cli_model="claude-haiku-4-5")
    assert policy.resolve(ModelHint.PLANNER, role="implementer") == "claude-haiku-4-5"


def test_precedence_per_role_over_env_and_defaults(project_root, monkeypatch):
    """per_role beats env overrides and defaults."""
    monkeypatch.setenv("NEXUS_MODEL_PLANNER", "claude-opus-4-8")
    yaml_path = project_root / ".nexus" / "policy.yaml"
    yaml_path.write_text("per_role:\n  implementer: claude-haiku-4-5\n")
    policy = ModelPolicy.load(project_root)
    assert policy.resolve(ModelHint.PLANNER, role="implementer") == "claude-haiku-4-5"


def test_precedence_env_over_defaults(project_root, monkeypatch):
    """env override beats DEFAULT_POLICY."""
    monkeypatch.setenv("NEXUS_MODEL_EVOLVER", "claude-opus-4-8")
    policy = ModelPolicy.load(project_root)
    assert policy.resolve(ModelHint.EVOLVER) == "claude-opus-4-8"


def test_falls_back_to_default_when_nothing_set(project_root):
    policy = ModelPolicy.load(project_root)
    assert policy.resolve(ModelHint.CRITIQUE) == "claude-sonnet-4-6"


def test_yaml_defaults_override_baked_defaults(project_root):
    """policy.yaml defaults: section overrides DEFAULT_POLICY at the policy layer."""
    yaml_path = project_root / ".nexus" / "policy.yaml"
    yaml_path.write_text("defaults:\n  planner: claude-opus-4-8\n")
    policy = ModelPolicy.load(project_root)
    assert policy.resolve(ModelHint.PLANNER) == "claude-opus-4-8"


def test_missing_policy_yaml_uses_defaults(tmp_path):
    """No .nexus/policy.yaml → DEFAULT_POLICY wins."""
    policy = ModelPolicy.load(tmp_path)  # no .nexus dir at all
    assert policy.resolve(ModelHint.PLANNER) == "claude-sonnet-4-6"


def test_malformed_yaml_does_not_crash(project_root):
    """Bad YAML → log warning, fall back to defaults (don't block startup)."""
    yaml_path = project_root / ".nexus" / "policy.yaml"
    yaml_path.write_text(":\n  this is: : not yaml [[[")
    policy = ModelPolicy.load(project_root)
    assert policy.resolve(ModelHint.PLANNER) == "claude-sonnet-4-6"


def test_unknown_hint_in_yaml_ignored(project_root):
    """Unknown hint names in YAML should be silently dropped, not crash."""
    yaml_path = project_root / ".nexus" / "policy.yaml"
    yaml_path.write_text("defaults:\n  not_a_real_hint: claude-opus-4-8\n  planner: claude-opus-4-8\n")
    policy = ModelPolicy.load(project_root)
    assert policy.resolve(ModelHint.PLANNER) == "claude-opus-4-8"


def test_create_default_yaml_writes_template(tmp_path):
    target = tmp_path / "policy.yaml"
    ModelPolicy.create_default_yaml(target)
    assert target.exists()
    text = target.read_text()
    assert "planner" in text
    assert "verifier_security" in text
    # All hints should be present in the template (commented or not)
    for hint in ModelHint:
        assert hint.value in text, f"hint {hint.value} missing from template"


def test_resolve_raises_when_no_policy_anywhere(tmp_path):
    """If a hint has neither env, role, nor default, resolve must raise."""
    # Construct a policy with no defaults for the hint we ask for.
    bad = ModelPolicy(defaults={}, env_overrides={}, per_role={}, cli_override=None)
    with pytest.raises(ValueError, match="No model resolved"):
        bad.resolve(ModelHint.PLANNER)