"""Tests for ModelRouter wiring into VerificationPipeline.

Covers the v1.2 feature-flagged ModelRouter path inside
``src.verification.pipeline``. The router is *opt-in* via
``NEXUS_USE_MODEL_ROUTER=1``; the default behavior must remain the
v1.1 "use the supplied delegate_task as-is" contract.

Four scenarios:

1. When the flag is OFF and no router is passed, the pipeline uses the
   user-supplied delegate_task verbatim — backwards-compat with v1.1.
2. When the flag is ON AND a router is passed, the SecurityScan kind
   routes with ``ModelHint.VERIFIER_SECURITY`` (cost-downgrade per
   v1.2 decision).
3. When the flag is ON AND a router is passed, every other kind
   (spec_compliance, logic_analysis, …) routes with
   ``ModelHint.VERIFIER_REVIEW``.
4. End-to-end: a pipeline constructed with ``model_router=`` and the
   flag on routes verifier steps via the router rather than the
   delegate_task (when both are supplied, the router wins).

All tests use a fake router (no network) — the goal is to verify the
pipeline's *dispatch logic*, not ModelRouter itself.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.verification.pipeline import (
    VerificationPipeline,
    _build_router_delegate,
    _is_router_enabled,
    _noop_delegate,
    _resolve_router_hint,
)
from src.llm.model_policy import ModelHint


# --------------------------------------------------------------------- fakes


class FakeRouter:
    """Minimal ModelRouter stub that records every route() call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def route(
        self,
        messages: list[dict],
        hint: ModelHint,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> tuple[str, Any]:
        self.calls.append(
            {
                "messages": messages,
                "hint": hint,
                "system_prompt": system_prompt,
            }
        )
        # Return a sentinel response — the pipeline only inspects success/result.
        return ("fake-model", {"ok": True})


def _make_pipeline(router: FakeRouter | None = None, delegate=None) -> VerificationPipeline:
    """Build a pipeline with safe defaults (no real subprocess)."""
    return VerificationPipeline(
        delegate_task=delegate or _noop_delegate,
        model_router=router,
    )


# --------------------------------------------------------------------- unit


def test_is_router_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("NEXUS_USE_MODEL_ROUTER", raising=False)
    assert _is_router_enabled() is False

    monkeypatch.setenv("NEXUS_USE_MODEL_ROUTER", "0")
    assert _is_router_enabled() is False

    monkeypatch.setenv("NEXUS_USE_MODEL_ROUTER", "1")
    assert _is_router_enabled() is True


def test_resolve_router_hint_security_kind():
    assert _resolve_router_hint({"scan_type": "security_deep_analysis"}) is ModelHint.VERIFIER_SECURITY


def test_resolve_router_hint_review_kinds():
    for ctx in (
        {"review_type": "spec_compliance"},
        {"review_type": "logic_analysis"},
        {},  # default fallback
        {"scan_type": "other"},  # unknown scan_type → review
    ):
        assert _resolve_router_hint(ctx) is ModelHint.VERIFIER_REVIEW, ctx


# ----------------------------------------------------------------- tests


def test_backwards_compat_no_op_when_flag_off(monkeypatch):
    """Flag unset + router supplied → router is NOT used.

    Without the flag the user-supplied delegate_task is the single source
    of truth. The router, if any, is ignored. This preserves v1.1 test
    contracts that rely on delegate_task being called verbatim.
    """
    monkeypatch.delenv("NEXUS_USE_MODEL_ROUTER", raising=False)
    router = FakeRouter()
    delegate = MagicMock(return_value={"success": True, "result": []})
    pipeline = _make_pipeline(router=router, delegate=delegate)

    # Delegate should be wired to the gates (not the router shim).
    assert pipeline._delegate_task is delegate
    assert pipeline._security_scan._delegate_task is delegate
    assert pipeline._review_gate._delegate_task is delegate

    # Call the delegate directly to confirm the pipeline still drives it.
    out = pipeline._delegate_task("task", {"code": "x"})
    delegate.assert_called_once()
    router.calls == []
    assert out == {"success": True, "result": []}


def test_router_delegate_dispatches_security_kind(monkeypatch):
    """Flag ON + router → SecurityScan kind resolves to VERIFIER_SECURITY."""
    monkeypatch.setenv("NEXUS_USE_MODEL_ROUTER", "1")
    router = FakeRouter()
    delegate = _build_router_delegate(router)

    delegate("scan", {"scan_type": "security_deep_analysis", "code": "x", "file_path": "a.py"})

    assert len(router.calls) == 1
    assert router.calls[0]["hint"] is ModelHint.VERIFIER_SECURITY


def test_router_delegate_dispatches_other_kinds(monkeypatch):
    """Flag ON + router → all other kinds resolve to VERIFIER_REVIEW."""
    monkeypatch.setenv("NEXUS_USE_MODEL_ROUTER", "1")
    router = FakeRouter()
    delegate = _build_router_delegate(router)

    delegate("spec", {"review_type": "spec_compliance", "code": "x", "file_path": "a.py"})
    delegate("logic", {"review_type": "logic_analysis", "code": "x", "file_path": "a.py"})
    # No kind → default still VERIFIER_REVIEW.
    delegate("unknown", {"code": "x"})

    assert len(router.calls) == 3
    for call in router.calls:
        assert call["hint"] is ModelHint.VERIFIER_REVIEW


def test_pipeline_routes_verifier_steps_correctly(monkeypatch):
    """Flag ON + router → pipeline uses router delegate, not the supplied one.

    This is the end-to-end test: a router injected at construction wins
    over ``delegate_task`` whenever the flag is on. The router delegate
    is the single point that decides VERIFIER_SECURITY vs VERIFIER_REVIEW
    based on the verify-step kind.
    """
    monkeypatch.setenv("NEXUS_USE_MODEL_ROUTER", "1")
    router = FakeRouter()
    user_delegate = MagicMock(return_value={"success": True, "result": []})
    pipeline = VerificationPipeline(
        delegate_task=user_delegate,
        model_router=router,
    )

    # Pipeline should NOT use the user delegate — router delegate wins.
    assert pipeline._delegate_task is not user_delegate
    user_delegate.assert_not_called()

    # The effective delegate should produce the right hint for both kinds.
    pipeline._delegate_task("scan", {"scan_type": "security_deep_analysis", "code": "x", "file_path": "a.py"})
    pipeline._delegate_task("spec", {"review_type": "spec_compliance", "code": "x", "file_path": "a.py"})

    assert len(router.calls) == 2
    assert router.calls[0]["hint"] is ModelHint.VERIFIER_SECURITY
    assert router.calls[1]["hint"] is ModelHint.VERIFIER_REVIEW