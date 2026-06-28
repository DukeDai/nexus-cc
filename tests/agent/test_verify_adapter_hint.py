"""Tests for verify_adapter ReviewGate delegate model_hint propagation.

These tests pin the v1.2 contract: when an LLM is injected into
``VerificationAdapter`` (i.e. the Router is enabled), the ReviewGate
delegate functions (``_delegate_spec_compliance`` and
``_delegate_logic_analysis``) forward ``ModelHint.VERIFIER_REVIEW`` to
the underlying LLM client so the router can pick the right model.

When no LLM is injected (legacy mode), the delegates return a benign
empty-result dict — behavior unchanged.

The security-review delegate additionally uses ``ModelHint.VERIFIER_SECURITY``
(per v1.2 cost-downgrade decision) and is covered by the same factory
in ``register_defaults``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.verify_adapter import (
    VerificationAdapter,
    _delegate_logic_analysis,
    _delegate_security_review,
    _delegate_spec_compliance,
)
from src.llm.model_policy import ModelHint


class HintCapturingLLM:
    """Records (system, messages, kwargs) for each .complete() call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, *, system: str, messages: list[dict], **kwargs):
        self.calls.append({"system": system, "messages": messages, "kwargs": dict(kwargs)})
        resp = MagicMock()
        resp.content = [MagicMock(text="[]")]
        return resp


@pytest.mark.asyncio
async def test_spec_compliance_delegate_routes_with_verifier_review_hint():
    """_delegate_spec_compliance forwards ModelHint.VERIFIER_REVIEW to the LLM by default."""
    llm = HintCapturingLLM()
    result = await _delegate_spec_compliance(
        "check spec",
        {"spec": {"requirements": ["foo"]}, "file_path": "src/x.py"},
        llm=llm,
    )

    assert result == {"success": True, "result": "[]"}
    assert len(llm.calls) == 1
    assert llm.calls[0]["kwargs"].get("model_hint") is ModelHint.VERIFIER_REVIEW


@pytest.mark.asyncio
async def test_logic_analysis_delegate_routes_with_verifier_review_hint():
    """_delegate_logic_analysis forwards ModelHint.VERIFIER_REVIEW to the LLM by default."""
    llm = HintCapturingLLM()
    result = await _delegate_logic_analysis(
        "analyze logic",
        {"code": "def f():\n    return 1", "file_path": "src/x.py"},
        llm=llm,
    )

    assert result == {"success": True, "result": "[]"}
    assert len(llm.calls) == 1
    assert llm.calls[0]["kwargs"].get("model_hint") is ModelHint.VERIFIER_REVIEW


@pytest.mark.asyncio
async def test_security_review_delegate_uses_verifier_security_hint():
    """_delegate_security_review uses VERIFIER_SECURITY (cost-downgrade per v1.2)."""
    llm = HintCapturingLLM()
    result = await _delegate_security_review(
        "scan security",
        {"code": "x = 1", "file_path": "src/x.py"},
        llm=llm,
    )

    assert result == {"success": True, "result": "[]"}
    assert len(llm.calls) == 1
    assert llm.calls[0]["kwargs"].get("model_hint") is ModelHint.VERIFIER_SECURITY


@pytest.mark.asyncio
async def test_delegate_with_no_llm_returns_empty_result():
    """When llm is None (legacy mode), delegates return benign no-op result."""
    result = await _delegate_spec_compliance(
        "check spec", {"spec": {}, "file_path": "src/x.py"}, llm=None
    )
    assert result == {"success": True, "result": []}


@pytest.mark.asyncio
async def test_register_defaults_combined_delegate_dispatches_by_review_type():
    """register_defaults wires the combined delegate so spec/logic/security route correctly."""
    llm = HintCapturingLLM()
    adapter = VerificationAdapter(wal=MagicMock(), llm=llm)
    adapter.register_defaults()

    pipeline = adapter._pipelines["review"]

    # Exercise each review_type branch through the combined delegate.
    captured: list[dict] = []

    async def capture_run(stage, ctx):
        # Inspect merged ctx to find the delegate_task, then call it.
        pass

    # The pipeline doesn't expose the delegate directly; pull it via the
    # bound method on the underlying VerificationPipeline.
    from src.verification import VerificationPipeline

    # VerificationPipeline stores delegate_task as _delegate_task
    underlying_delegate = None
    for name in ("security", "review"):
        p = adapter._pipelines[name]
        if isinstance(p, VerificationPipeline):
            underlying_delegate = getattr(p, "_delegate_task", None) or getattr(p, "delegate_task", None)
            if underlying_delegate is not None:
                break

    assert underlying_delegate is not None, "delegate_task not found on underlying pipeline"

    # Spec compliance path
    await underlying_delegate("spec task", {"review_type": "spec_compliance", "spec": {}, "file_path": "src/x.py"})
    # Logic analysis path
    await underlying_delegate("logic task", {"review_type": "logic_analysis", "code": "x = 1", "file_path": "src/x.py"})
    # Security path
    await underlying_delegate("sec task", {"review_type": "security", "code": "x = 1", "file_path": "src/x.py"})

    hints = [c["kwargs"].get("model_hint") for c in llm.calls]
    assert ModelHint.VERIFIER_REVIEW in hints
    assert ModelHint.VERIFIER_SECURITY in hints
    # 3 calls (spec, logic, security) and 2 are VERIFIER_REVIEW, 1 is VERIFIER_SECURITY.
    assert hints.count(ModelHint.VERIFIER_REVIEW) == 2
    assert hints.count(ModelHint.VERIFIER_SECURITY) == 1