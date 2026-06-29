"""Integration: ModelRouter.route() emits CostRecords that land in a mock WAL.

Verifies the v1.2 routing pipeline end-to-end:
- Plan with one step per ModelHint kind (PLANNER, CRITIQUE, VERIFIER_REVIEW, VERIFIER_SECURITY).
- ModelPolicy mapping: PLANNER -> MiniMax-M3, VERIFIER_SECURITY -> MiniMax-M2.7,
  others -> MiniMax-M3.
- ModelRouter.route() called for each hint.
- The mock WAL's `append_cost` is invoked once per routed call with the right
  payload (model, hint, tokens, cost_usd).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.plan import Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.llm.cost_tracker import CostRecord, CostTracker, estimate_cost
from src.llm.model_policy import ModelHint, ModelPolicy
from src.llm.model_router import ModelRouter
from src.llm.client import Response, Usage


# --------------------------------------------------------------------- helpers


def _make_plan() -> Plan:
    """One step per required ModelHint kind."""
    return Plan(
        plan_id=new_plan_id(),
        spec="router cost wal integration",
        steps=[
            PlanStep(id=new_step_id(), kind=PlanStepKind.CRITIQUE, intent="critique step"),
            PlanStep(id=new_step_id(), kind=PlanStepKind.VERIFY, intent="verifier review step"),
            # Two verify-like steps distinguished by role: REVIEW vs SECURITY
            PlanStep(id=new_step_id(), kind=PlanStepKind.VERIFY, intent="verifier security step"),
            # A plan-level "planner" step (treated as the planning hint)
            PlanStep(id=new_step_id(), kind=PlanStepKind.TOOL, intent="planner step", tool="Bash"),
        ],
    )


def _make_policy() -> ModelPolicy:
    """PLANNER & others -> MiniMax-M3; VERIFIER_SECURITY -> MiniMax-M2.7 (cost-down)."""
    return ModelPolicy(
        cli_override=None,
        per_role={},
        defaults={
            ModelHint.PLANNER: "MiniMax-M3",
            ModelHint.CRITIQUE: "MiniMax-M3",
            ModelHint.VERIFIER_REVIEW: "MiniMax-M3",
            ModelHint.VERIFIER_SECURITY: "MiniMax-M2.7",
            ModelHint.EVOLVER: "MiniMax-M3",
        },
        env_overrides={},
    )


def _fake_complete(self, messages, tools=None, system_prompt="", **kwargs):
    """Stub LLMClient.complete() — return a deterministic Usage."""
    return Response(
        content="ok",
        tool_calls=[],
        finish_reason="stop",
        usage=Usage(input_tokens=100, output_tokens=50),
    )


# --------------------------------------------------------------------- tests


@pytest.fixture
def mock_wal() -> MagicMock:
    """A MagicMock WAL exposing the append_cost() sink."""
    wal = MagicMock()
    wal.append_cost = MagicMock()
    return wal


def test_router_routes_each_hint_to_policy_model(mock_wal: MagicMock, tmp_path: Path) -> None:
    """ModelRouter.route() must call wal.append_cost() with the resolved model."""
    plan = _make_plan()
    policy = _make_policy()
    tracker = CostTracker(project_root=tmp_path, wal=mock_wal, buffer_size=100)
    router = ModelRouter(policy=policy, cost_tracker=tracker)

    hint_to_expected = {
        ModelHint.PLANNER: "MiniMax-M3",
        ModelHint.CRITIQUE: "MiniMax-M3",
        ModelHint.VERIFIER_REVIEW: "MiniMax-M3",
        ModelHint.VERIFIER_SECURITY: "MiniMax-M2.7",
    }

    with patch("src.llm.client.LLMClient.complete", new=_fake_complete):
        for hint, expected_model in hint_to_expected.items():
            model, _resp = router.route(
                messages=[{"role": "user", "content": f"hi {hint.value}"}],
                hint=hint,
                system_prompt="s",
            )
            assert model == expected_model, f"hint={hint.value} routed to {model!r}"

    # append_cost should have been called once per routed call (4 total).
    assert mock_wal.append_cost.call_count == len(hint_to_expected)
    # Sanity: tracker buffer mirrors the WAL.
    assert len(tracker.records) == len(hint_to_expected)


def test_router_cost_records_carry_hint_and_tokens(mock_wal: MagicMock, tmp_path: Path) -> None:
    """Each WAL cost record must carry the correct hint, model, and token counts."""
    policy = _make_policy()
    tracker = CostTracker(project_root=tmp_path, wal=mock_wal, buffer_size=100)
    router = ModelRouter(policy=policy, cost_tracker=tracker)

    with patch("src.llm.client.LLMClient.complete", new=_fake_complete):
        model, _ = router.route(
            messages=[{"role": "user", "content": "security check"}],
            hint=ModelHint.VERIFIER_SECURITY,
            system_prompt="s",
        )

    # The mock WAL was called once.
    mock_wal.append_cost.assert_called_once()
    payload = mock_wal.append_cost.call_args.args[0]
    assert payload["model"] == "MiniMax-M2.7"
    assert payload["hint"] == "verifier_security"
    assert payload["prompt_tokens"] == 100
    assert payload["completion_tokens"] == 50
    # cost_usd is computed from PRICING_PER_1K_TOKENS
    expected_cost = estimate_cost("MiniMax-M2.7", 100, 50)
    assert payload["cost_usd"] == pytest.approx(expected_cost)


def test_router_cost_records_distinguish_planner_and_security(mock_wal: MagicMock, tmp_path: Path) -> None:
    """Two distinct hints must produce two distinct cost records (different model, different cost)."""
    policy = _make_policy()
    tracker = CostTracker(project_root=tmp_path, wal=mock_wal, buffer_size=100)
    router = ModelRouter(policy=policy, cost_tracker=tracker)

    with patch("src.llm.client.LLMClient.complete", new=_fake_complete):
        router.route(messages=[{"role": "user", "content": "p"}], hint=ModelHint.PLANNER, system_prompt="s")
        router.route(messages=[{"role": "user", "content": "s"}], hint=ModelHint.VERIFIER_SECURITY, system_prompt="s")

    assert mock_wal.append_cost.call_count == 2
    models = [c.args[0]["model"] for c in mock_wal.append_cost.call_args_list]
    assert models == ["MiniMax-M3", "MiniMax-M2.7"]

    # SECURITY pricing is cheaper than PLANNER at equal token counts.
    p_cost = mock_wal.append_cost.call_args_list[0].args[0]["cost_usd"]
    s_cost = mock_wal.append_cost.call_args_list[1].args[0]["cost_usd"]
    assert s_cost < p_cost, f"expected security ({s_cost}) cheaper than planner ({p_cost})"


def test_router_aggregate_by_hint_after_routing(mock_wal: MagicMock, tmp_path: Path) -> None:
    """CostTracker.aggregate_by('hint') should reflect all routed hints."""
    policy = _make_policy()
    tracker = CostTracker(project_root=tmp_path, wal=mock_wal, buffer_size=100)
    router = ModelRouter(policy=policy, cost_tracker=tracker)

    with patch("src.llm.client.LLMClient.complete", new=_fake_complete):
        for hint in (ModelHint.PLANNER, ModelHint.CRITIQUE, ModelHint.VERIFIER_REVIEW, ModelHint.VERIFIER_SECURITY):
            router.route(messages=[{"role": "user", "content": "x"}], hint=hint, system_prompt="s")

    agg = tracker.aggregate_by("hint")
    # All four hint buckets should be present.
    for hint in ("planner", "critique", "verifier_review", "verifier_security"):
        assert hint in agg, f"missing hint bucket: {hint}"
        assert agg[hint]["count"] == 1
        assert agg[hint]["prompt_tokens"] == 100
        assert agg[hint]["completion_tokens"] == 50


def test_router_wal_failure_does_not_break_call(mock_wal: MagicMock, tmp_path: Path) -> None:
    """If wal.append_cost() raises, the LLM call must still return successfully."""
    policy = _make_policy()
    tracker = CostTracker(project_root=tmp_path, wal=mock_wal, buffer_size=100)
    router = ModelRouter(policy=policy, cost_tracker=tracker)

    # First call to append_cost raises; subsequent calls also raise to be sure.
    mock_wal.append_cost.side_effect = RuntimeError("disk full")

    with patch("src.llm.client.LLMClient.complete", new=_fake_complete):
        model, resp = router.route(
            messages=[{"role": "user", "content": "x"}],
            hint=ModelHint.PLANNER,
            system_prompt="s",
        )
    # The call itself succeeded.
    assert model == "MiniMax-M3"
    assert resp.content == "ok"
    # But the WAL was attempted.
    assert mock_wal.append_cost.called
    # The in-memory buffer still received the record (WAL failure is non-fatal).
    assert len(tracker.records) == 1
