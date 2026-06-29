"""Tests for the step → model resolution logic in plan_panel.py.

Exercises the full matrix of step kinds × pipeline × role × policy
configurations so the plan-panel model badge never silently disappears.
"""
from __future__ import annotations

import pytest

from src.agent.plan import OnFailure, PlanStep, PlanStepKind
from src.agents.base import AgentRole
from src.llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy
from src.tui.plan_panel import _resolve_step_model, _short_model_tag


def _step(
    kind: PlanStepKind,
    *,
    role: AgentRole | None = None,
    pipeline: str | None = None,
    intent: str = "do thing",
) -> PlanStep:
    return PlanStep(
        id=f"step_{kind.value}_{intent[:8]}",
        kind=kind,
        intent=intent,
        tool="Bash" if kind == PlanStepKind.TOOL else None,
        args={},
        role=role,
        pipeline=pipeline,
        success_criteria="ok",
        on_failure=OnFailure.ASK,
        timeout_s=60,
    )


# -------------------------------------------------------------- per-kind

def test_ask_user_returns_none():
    """ASK_USER never invokes a model — badge should be absent."""
    step = _step(PlanStepKind.ASK_USER)
    assert _resolve_step_model(step, policy=None) is None
    assert _resolve_step_model(step, policy=ModelPolicy()) is None


def test_tool_step_uses_planner_default():
    step = _step(PlanStepKind.TOOL)
    model = _resolve_step_model(step, policy=None)
    assert model == DEFAULT_POLICY[ModelHint.PLANNER]


def test_critique_step_uses_critique_default():
    step = _step(PlanStepKind.CRITIQUE)
    model = _resolve_step_model(step, policy=None)
    assert model == DEFAULT_POLICY[ModelHint.CRITIQUE]


def test_verify_review_pipeline_uses_verifier_review():
    step = _step(PlanStepKind.VERIFY, pipeline="review")
    model = _resolve_step_model(step, policy=None)
    assert model == DEFAULT_POLICY[ModelHint.VERIFIER_REVIEW]


@pytest.mark.parametrize("pipeline", ["tdd", "test", "review"])
def test_verify_non_security_pipelines_use_verifier_review(pipeline):
    step = _step(PlanStepKind.VERIFY, pipeline=pipeline)
    model = _resolve_step_model(step, policy=None)
    assert model == DEFAULT_POLICY[ModelHint.VERIFIER_REVIEW]


def test_verify_security_pipeline_uses_verifier_security():
    """Security verification deliberately downgrades to a cheap model (v1.2 spec §1.2)."""
    step = _step(PlanStepKind.VERIFY, pipeline="security")
    model = _resolve_step_model(step, policy=None)
    assert model == DEFAULT_POLICY[ModelHint.VERIFIER_SECURITY]
    # And it should differ from the default verifier_review model.
    assert model != DEFAULT_POLICY[ModelHint.VERIFIER_REVIEW]


def test_verify_without_pipeline_falls_back_to_verifier_review():
    step = _step(PlanStepKind.VERIFY, pipeline=None)
    model = _resolve_step_model(step, policy=None)
    assert model == DEFAULT_POLICY[ModelHint.VERIFIER_REVIEW]


# ---------------------------------------------------------------- SUBPLAN

def test_subplan_with_role_hits_per_role_override():
    """per_role[role.name] wins over the PLANNER hint default."""
    policy = ModelPolicy()
    policy.per_role["SECURITY"] = "claude-opus-4-8"
    step = _step(PlanStepKind.SUBPLAN, role=AgentRole.SECURITY)
    model = _resolve_step_model(step, policy=policy)
    assert model == "claude-opus-4-8"


def test_subplan_with_role_no_per_role_hit_falls_back_to_planner():
    """SUBPLAN + role but no per_role entry → resolve(hint=PLANNER)."""
    policy = ModelPolicy()
    # Empty per_role — no match.
    step = _step(PlanStepKind.SUBPLAN, role=AgentRole.IMPLEMENTER)
    model = _resolve_step_model(step, policy=policy)
    assert model == DEFAULT_POLICY[ModelHint.PLANNER]


def test_subplan_without_role_uses_planner():
    step = _step(PlanStepKind.SUBPLAN, role=None)
    model = _resolve_step_model(step, policy=None)
    assert model == DEFAULT_POLICY[ModelHint.PLANNER]


# ------------------------------------------------------------------ policy

def test_cli_override_wins_for_tool_step():
    policy = ModelPolicy(cli_override="claude-haiku-4-5")
    step = _step(PlanStepKind.TOOL)
    model = _resolve_step_model(step, policy=policy)
    assert model == "claude-haiku-4-5"


def test_policy_defaults_override_baked_in_defaults():
    policy = ModelPolicy()
    policy.defaults[ModelHint.PLANNER] = "MiniMax-M3"
    step = _step(PlanStepKind.TOOL)
    model = _resolve_step_model(step, policy=policy)
    assert model == "MiniMax-M3"


def test_policy_with_unresolvable_hint_falls_back_to_default():
    """If policy.resolve raises ValueError, we still surface DEFAULT_POLICY."""
    policy = ModelPolicy()
    policy.defaults = {}  # make PLANNER unresolvable through policy
    step = _step(PlanStepKind.TOOL)
    model = _resolve_step_model(step, policy=policy)
    # DEFAULT_POLICY still has PLANNER — so the badge isn't lost.
    assert model == DEFAULT_POLICY[ModelHint.PLANNER]


def test_minimax_policy_resolves_correctly():
    """End-to-end with the MiniMax policy: PLANNER → MiniMax-M3,
    VERIFIER_SECURITY → MiniMax-M2.7 (per the v1.2 spec's deliberate
    cost-downgrade for security checks)."""
    policy = ModelPolicy()
    policy.defaults = {
        ModelHint.PLANNER: "MiniMax-M3",
        ModelHint.CRITIQUE: "MiniMax-M3",
        ModelHint.VERIFIER_REVIEW: "MiniMax-M3",
        ModelHint.VERIFIER_SECURITY: "MiniMax-M2.7",
        ModelHint.EVOLVER: "MiniMax-M3",
    }
    assert _resolve_step_model(_step(PlanStepKind.TOOL), policy) == "MiniMax-M3"
    assert _resolve_step_model(_step(PlanStepKind.CRITIQUE), policy) == "MiniMax-M3"
    assert (
        _resolve_step_model(_step(PlanStepKind.VERIFY, pipeline="security"), policy)
        == "MiniMax-M2.7"
    )
    assert (
        _resolve_step_model(_step(PlanStepKind.VERIFY, pipeline="review"), policy)
        == "MiniMax-M3"
    )


# ------------------------------------------------------- badge mapping

@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-sonnet-4-6", "Sonnet"),
        ("claude-haiku-4-5", "Haiku"),
        ("claude-opus-4-8", "Opus"),
        ("MiniMax-M3", "M3"),
        ("MiniMax-M2.7", "M2.7"),
        ("gpt-4o-mini", "mini"),
        ("", "?"),
    ],
)
def test_short_model_tag_mapping(model, expected):
    assert _short_model_tag(model) == expected
