"""Tests for ModelMappingModal (v1.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy
from src.tui.model_mapping_modal import ModelMappingModal


def test_modal_initializes_with_policy_defaults(tmp_path: Path):
    policy = ModelPolicy.load(tmp_path)
    modal = ModelMappingModal(policy=policy, project_root=tmp_path)
    # Working defaults should match the policy's defaults for every hint.
    for hint in ModelHint:
        assert modal._working_defaults[hint] == policy.defaults.get(
            hint, DEFAULT_POLICY[hint]
        )


def test_modal_initial_working_defaults_cover_every_hint(tmp_path: Path):
    """Each ModelHint should have a working default seeded from the policy."""
    policy = ModelPolicy.load(tmp_path)
    modal = ModelMappingModal(policy=policy, project_root=tmp_path)
    assert len(modal._working_defaults) == len(ModelHint)
    for hint in ModelHint:
        assert hint in modal._working_defaults
        assert modal._working_defaults[hint]  # non-empty


def test_modal_save_persists_to_policy_yaml(tmp_path: Path):
    """Saving a new mapping should write a defaults: section to policy.yaml."""
    policy = ModelPolicy.load(tmp_path)
    modal = ModelMappingModal(policy=policy, project_root=tmp_path)
    # Override planner to a custom model.
    new_planner = "claude-opus-4-8"
    modal._working_defaults[ModelHint.PLANNER] = new_planner
    # Persist directly (skip Textual's event loop).
    new_defaults = dict(modal._working_defaults)
    modal._write_policy_yaml(new_defaults)

    policy_path = tmp_path / ".nexus" / "policy.yaml"
    assert policy_path.exists()
    import yaml
    data = yaml.safe_load(policy_path.read_text())
    assert "defaults" in data
    assert data["defaults"]["planner"] == new_planner
    # Other hints should still be present.
    for hint in ModelHint:
        assert hint.value in data["defaults"]


def test_modal_save_preserves_existing_per_role_section(tmp_path: Path):
    """Writing defaults must not clobber an existing per_role section."""
    policy_path = tmp_path / ".nexus" / "policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        "per_role:\n  implementer: claude-sonnet-4-6\ndefaults:\n  planner: claude-sonnet-4-6\n"
    )
    policy = ModelPolicy.load(tmp_path)
    modal = ModelMappingModal(policy=policy, project_root=tmp_path)
    modal._working_defaults[ModelHint.PLANNER] = "claude-opus-4-8"
    modal._write_policy_yaml(dict(modal._working_defaults))

    import yaml
    data = yaml.safe_load(policy_path.read_text())
    assert data["per_role"]["implementer"] == "claude-sonnet-4-6"
    assert data["defaults"]["planner"] == "claude-opus-4-8"


def test_modal_short_model_tag():
    """Sanity check the badge mapping used by PlanPanel."""
    from src.tui.plan_panel import _short_model_tag
    assert _short_model_tag("claude-sonnet-4-6") == "Sonnet"
    assert _short_model_tag("claude-haiku-4-5") == "Haiku"
    assert _short_model_tag("claude-opus-4-8") == "Opus"
    assert _short_model_tag("gpt-4o-mini") == "mini"
    assert _short_model_tag("") == "?"
