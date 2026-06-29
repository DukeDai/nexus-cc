"""Tests for src.llm.model_router (v1.2 hint-based API)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy
from src.llm.model_router import ModelRouter


@pytest.fixture
def tracker(tmp_path: Path) -> CostTracker:
    return CostTracker(project_root=tmp_path, wal=None, buffer_size=100)


@pytest.fixture
def policy() -> ModelPolicy:
    return ModelPolicy()


@pytest.fixture
def router(policy: ModelPolicy, tracker: CostTracker) -> ModelRouter:
    return ModelRouter(policy=policy, cost_tracker=tracker)


@pytest.fixture(autouse=True)
def _strip_env(monkeypatch):
    for hint in ModelHint:
        monkeypatch.delenv(f"NEXUS_MODEL_{hint.value.upper()}", raising=False)


def _fake_response(model="claude-sonnet-4-6", prompt=10, completion=20):
    """Build a Response-shaped MagicMock that mimics LLMClient.complete output."""
    from src.llm.client import Response, ToolCall, Usage

    return Response(
        content="ok",
        tool_calls=[],
        finish_reason="stop",
        usage=Usage(input_tokens=prompt, output_tokens=completion),
    )


def test_default_models_exposes_anthropic_and_minimax(router: ModelRouter):
    """v1.2 surface: Anthropic + MiniMax family (Anthropic-compatible API)."""
    available = router.get_available_models()
    assert all("gpt" not in m for m in available)
    assert all("llama" not in m for m in available)
    assert all("mistral" not in m for m in available)
    # Anthropic family still first-class.
    assert "claude-haiku-4-5" in available
    assert "claude-sonnet-4-6" in available
    assert "claude-opus-4-8" in available
    # MiniMax family re-added (Anthropic-compatible API).
    assert "MiniMax-M3" in available
    assert "MiniMax-M2.7" in available


def test_minimax_routes_via_minimax_cn_provider(router: ModelRouter, monkeypatch):
    """Resolving MiniMax-M3 must produce an LLMClient with Provider.MINIMAX_CN."""
    from src.llm.client import LLMClient, Provider

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-cp-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    client = router.get_client("MiniMax-M3")
    assert isinstance(client, LLMClient)
    assert client.provider == Provider.MINIMAX_CN
    assert client.model == "MiniMax-M3"
    # Default base URL is MiniMax's Anthropic-compatible endpoint.
    assert "minimaxi.com" in client.base_url or "minimax" in client.base_url.lower()


def test_minimax_resolves_via_policy_defaults(router: ModelRouter):
    """policy.yaml defaults can map hints to MiniMax-M3."""
    from src.llm.client import LLMClient, Response, Usage

    router.policy.defaults = {h: "MiniMax-M3" for h in ModelHint}
    with patch.object(LLMClient, "complete", return_value=_fake_response("MiniMax-M3")):
        model_name, response = router.route(
            messages=[{"role": "user", "content": "hi"}],
            hint=ModelHint.PLANNER,
        )
    assert model_name == "MiniMax-M3"
    assert response.content == "ok"


def test_minimax_cost_record_uses_minimax_pricing(router: ModelRouter, tracker: CostTracker):
    """CostRecord for MiniMax-M3 must use the MiniMax pricing tier (Sonnet 4.6-equivalent)."""
    from src.llm.client import LLMClient

    router.policy.cli_override = "MiniMax-M3"
    with patch.object(LLMClient, "complete", return_value=_fake_response("MiniMax-M3", prompt=1000, completion=500)):
        router.route(messages=[{"role": "user", "content": "x"}], hint=ModelHint.PLANNER)
    assert len(tracker.records) == 1
    rec = tracker.records[0]
    assert rec.model == "MiniMax-M3"
    # Sonnet 4.6-equivalent: 0.003/1k in + 0.015/1k out
    # 1000 * 0.003 / 1000 + 500 * 0.015 / 1000 = 0.003 + 0.0075 = 0.0105
    assert abs(rec.cost_usd - 0.0105) < 1e-9


def test_env_key_falls_back_to_minimax_api_key(router: ModelRouter, monkeypatch):
    """_env_key should pick up MINIMAX_API_KEY when Anthropic vars are unset."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test-key")
    assert router._env_key() == "minimax-test-key"


def test_route_resolves_each_hint(router: ModelRouter):
    """Every ModelHint maps to its DEFAULT_POLICY model via route()."""
    from src.llm.client import LLMClient

    for hint, expected_model in DEFAULT_POLICY.items():
        # Stub LLMClient.complete so we don't hit the network
        with patch.object(LLMClient, "complete", return_value=_fake_response(expected_model)):
            model_name, response = router.route(
                messages=[{"role": "user", "content": "hi"}],
                hint=hint,
            )
        assert model_name == expected_model, f"hint={hint} resolved to {model_name}"
        assert response.content == "ok"


def test_route_uses_role_override(router: ModelRouter):
    """per_role[role] beats the hint's default."""
    router.policy.per_role["implementer"] = "claude-opus-4-8"
    from src.llm.client import LLMClient

    with patch.object(LLMClient, "complete", return_value=_fake_response("claude-opus-4-8")):
        model_name, _ = router.route(
            messages=[{"role": "user", "content": "x"}],
            hint=ModelHint.PLANNER,
            role="implementer",
        )
    assert model_name == "claude-opus-4-8"


def test_route_uses_cli_override(router: ModelRouter):
    """cli_override beats everything."""
    router.policy.cli_override = "claude-haiku-4-5"
    from src.llm.client import LLMClient

    with patch.object(LLMClient, "complete", return_value=_fake_response("claude-haiku-4-5")):
        model_name, _ = router.route(
            messages=[{"role": "user", "content": "x"}],
            hint=ModelHint.PLANNER,  # default would be sonnet
        )
    assert model_name == "claude-haiku-4-5"


def test_route_emits_cost_record(router: ModelRouter, tracker: CostTracker):
    """Each non-streaming route() call appends a CostRecord with right hint+model."""
    from src.llm.client import LLMClient

    with patch.object(LLMClient, "complete", return_value=_fake_response("claude-sonnet-4-6", prompt=100, completion=50)):
        router.route(messages=[{"role": "user", "content": "x"}], hint=ModelHint.PLANNER)
    assert len(tracker.records) == 1
    rec = tracker.records[0]
    assert rec.model == "claude-sonnet-4-6"
    assert rec.hint == ModelHint.PLANNER
    assert rec.prompt_tokens == 100
    assert rec.completion_tokens == 50


def test_route_with_missing_policy_raises(router: ModelRouter):
    """Empty defaults + no env override → resolve() raises → route() propagates."""
    router.policy.defaults = {}
    router.policy.env_overrides = {}
    from src.llm.client import LLMClient

    with patch.object(LLMClient, "complete", return_value=_fake_response()):
        with pytest.raises(ValueError, match="No model resolved"):
            router.route(messages=[{"role": "user", "content": "x"}], hint=ModelHint.PLANNER)


def test_select_model_backwards_compat(router: ModelRouter):
    """v1.1 callers using select_model() still get a model name back."""
    name = router.select_model(task_type="any")  # type: ignore[arg-type]
    assert name == DEFAULT_POLICY[ModelHint.PLANNER]


def test_get_client_caches(router: ModelRouter):
    """Repeated get_client(model) returns the same instance."""
    a = router.get_client("claude-sonnet-4-6")
    b = router.get_client("claude-sonnet-4-6")
    assert a is b


def test_get_client_unknown_raises(router: ModelRouter):
    with pytest.raises(ValueError, match="Unknown model"):
        router.get_client("not-a-real-model")


def test_env_override_used_when_no_cli_no_role(router: ModelRouter, monkeypatch):
    monkeypatch.setenv("NEXUS_MODEL_CRITIQUE", "claude-opus-4-8")
    router.policy = ModelPolicy.load(Path("."))
    assert router.policy.resolve(ModelHint.CRITIQUE) == "claude-opus-4-8"


def test_route_legacy_uses_model_hint(router: ModelRouter):
    """route_legacy with model_hint= uses that model, ignores policy."""
    from src.llm.client import LLMClient

    with patch.object(LLMClient, "complete", return_value=_fake_response("claude-opus-4-8")):
        model_name, _ = router.route_legacy(
            messages=[{"role": "user", "content": "x"}],
            model_hint="claude-opus-4-8",
        )
    assert model_name == "claude-opus-4-8"
    # cli_override should be restored
    assert router.policy.cli_override is None


def test_estimate_cost_passthrough(router: ModelRouter):
    cost = router.estimate_cost("claude-sonnet-4-6", 1000, 500)
    assert cost > 0


def test_clear_client_cache(router: ModelRouter):
    """clear_client_cache should not crash and should drop the cache."""
    a = router.get_client("claude-sonnet-4-6")
    router.clear_client_cache()
    b = router.get_client("claude-sonnet-4-6")
    assert a is not b  # fresh instance after clear