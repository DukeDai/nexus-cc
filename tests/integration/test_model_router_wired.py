"""Integration test: feature flag routes through ModelRouter → LLMClient.

With NEXUS_USE_MODEL_ROUTER=1, `_build_llm_client` returns a _RouterAdapter.
The adapter's .complete() should invoke LLMClient.complete() with a model
matching DEFAULT_POLICY[ModelHint.PLANNER] ("claude-sonnet-4-6").
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.llm.client import Response, Usage
from src.llm.model_policy import DEFAULT_POLICY, ModelHint


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / ".nexus").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip NEXUS_MODEL_* env vars to prevent leakage from sibling tests."""
    from src.llm.model_policy import ModelHint
    for hint in ModelHint:
        monkeypatch.delenv(f"NEXUS_MODEL_{hint.value.upper()}", raising=False)


def test_router_injected_when_flag_on(project_root, monkeypatch):
    """NEXUS_USE_MODEL_ROUTER=1 → _RouterAdapter → route() uses PLANNER model."""
    from src.cli.commands.run import _build_llm_client

    # Auth + flag
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("NEXUS_USE_MODEL_ROUTER", "1")

    llm = _build_llm_client(project_root=project_root, wal=None)
    assert llm is not None

    # The adapter should be a _RouterAdapter
    from src.cli.commands.run import _RouterAdapter

    assert isinstance(llm, _RouterAdapter), f"expected _RouterAdapter, got {type(llm).__name__}"

    # Stub LLMClient.complete and capture what model_name was used.
    captured: dict = {}

    def fake_complete(self, messages, tools=None, system_prompt="", **kwargs):
        captured["model"] = self.model
        captured["provider"] = self.provider.value
        return Response(
            content="hello",
            tool_calls=[],
            finish_reason="stop",
            usage=Usage(input_tokens=10, output_tokens=20),
        )

    with patch("src.llm.client.LLMClient.complete", new=fake_complete):
        response = asyncio.run(
            llm.complete(
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
            )
        )

    expected_model = DEFAULT_POLICY[ModelHint.PLANNER]
    assert captured["model"] == expected_model, (
        f"Router routed to {captured['model']!r}, expected {expected_model!r}"
    )
    assert captured["provider"] == "anthropic"
    assert response.content == "hello"


def test_router_not_used_when_flag_off(project_root, monkeypatch):
    """NEXUS_USE_MODEL_ROUTER=0 → existing _AnthropicLLM (no Router)."""
    from src.cli.commands.run import _AnthropicLLM, _build_llm_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("NEXUS_USE_MODEL_ROUTER", raising=False)

    llm = _build_llm_client(project_root=project_root, wal=None)
    assert isinstance(llm, _AnthropicLLM)


def test_router_emits_cost_record_when_used(project_root, monkeypatch, tmp_path):
    """CostTracker should have one record after a successful Router call."""
    from src.cli.commands.run import _build_llm_client
    from src.context.wal import WALManager

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("NEXUS_USE_MODEL_ROUTER", "1")

    wal_path = tmp_path / "wal.jsonl"
    wal = WALManager(path=wal_path)
    llm = _build_llm_client(project_root=project_root, wal=wal)
    assert llm is not None

    def fake_complete(self, messages, tools=None, system_prompt="", **kwargs):
        return Response(
            content="ok",
            tool_calls=[],
            finish_reason="stop",
            usage=Usage(input_tokens=100, output_tokens=50),
        )

    with patch("src.llm.client.LLMClient.complete", new=fake_complete):
        asyncio.run(llm.complete(system="s", messages=[{"role": "user", "content": "x"}]))

    # WAL should have one cost_record entry (not a step_complete)
    text = wal_path.read_text()
    assert '"kind": "cost_record"' in text
    assert '"hint": "planner"' in text
    assert '"model": "claude-sonnet-4-6"' in text