"""Tests for src.llm.cost_tracker."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.llm.cost_tracker import (
    PRICING_PER_1K_TOKENS,
    CostRecord,
    CostTracker,
    estimate_cost,
    make_record,
)
from src.llm.model_policy import ModelHint


@pytest.fixture
def tracker(tmp_path: Path) -> CostTracker:
    return CostTracker(project_root=tmp_path, wal=None, buffer_size=5)


def _record(model="claude-sonnet-4-6", hint=ModelHint.PLANNER, role="implementer",
            prompt=1000, completion=500, ts=0.0) -> CostRecord:
    return CostRecord(
        model=model,
        hint=hint,
        role=role,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=estimate_cost(model, prompt, completion),
        timestamp=ts,
    )


def test_emit_appends_to_buffer(tracker: CostTracker):
    rec = _record()
    tracker.emit(rec)
    assert tracker.records == [rec]


def test_emit_calls_wal_append_cost(tmp_path):
    wal = MagicMock()
    t = CostTracker(project_root=tmp_path, wal=wal, buffer_size=100)
    rec = _record()
    t.emit(rec)
    wal.append_cost.assert_called_once()
    payload = wal.append_cost.call_args[0][0]
    assert payload["model"] == "claude-sonnet-4-6"
    assert payload["hint"] == "planner"  # serialized via .to_dict()


def test_wal_append_failure_does_not_break_emit(tmp_path):
    wal = MagicMock()
    wal.append_cost.side_effect = RuntimeError("disk full")
    t = CostTracker(project_root=tmp_path, wal=wal, buffer_size=100)
    # Should not raise
    t.emit(_record())
    assert len(t.records) == 1


def test_ring_buffer_overflow_drops_oldest(tmp_path):
    """buffer_size=2 → third emit drops the first."""
    t = CostTracker(project_root=tmp_path, wal=None, buffer_size=2)
    a = _record(ts=1.0)
    b = _record(ts=2.0)
    c = _record(ts=3.0)
    t.emit(a)
    t.emit(b)
    t.emit(c)
    assert list(t._buffer) == [b, c]


def test_aggregate_by_model(tracker: CostTracker):
    tracker.emit(_record(model="claude-sonnet-4-6", prompt=100, completion=50))
    tracker.emit(_record(model="claude-sonnet-4-6", prompt=200, completion=100))
    tracker.emit(_record(model="claude-haiku-4-5", prompt=500, completion=200))
    out = tracker.aggregate_by("model")
    assert out["claude-sonnet-4-6"]["prompt_tokens"] == 300
    assert out["claude-sonnet-4-6"]["completion_tokens"] == 150
    assert out["claude-sonnet-4-6"]["count"] == 2
    assert out["claude-haiku-4-5"]["count"] == 1


def test_aggregate_by_hint(tracker: CostTracker):
    tracker.emit(_record(hint=ModelHint.PLANNER))
    tracker.emit(_record(hint=ModelHint.PLANNER))
    tracker.emit(_record(hint=ModelHint.CRITIQUE))
    out = tracker.aggregate_by("hint")
    assert out["planner"]["count"] == 2
    assert out["critique"]["count"] == 1


def test_aggregate_by_role(tracker: CostTracker):
    tracker.emit(_record(role="implementer"))
    tracker.emit(_record(role="security"))
    tracker.emit(_record(role=None))
    out = tracker.aggregate_by("role")
    assert out["implementer"]["count"] == 1
    assert out["security"]["count"] == 1
    assert out["<none>"]["count"] == 1


def test_aggregate_by_session_buckets_by_minute(tracker: CostTracker):
    tracker.emit(_record(ts=100.0))
    tracker.emit(_record(ts=120.0))
    tracker.emit(_record(ts=200.0))
    out = tracker.aggregate_by("session")
    # 100//60 = 1, 120//60 = 2, 200//60 = 3
    assert sorted(out.keys()) == ["1", "2", "3"]


def test_aggregate_unknown_dimension_raises(tracker: CostTracker):
    with pytest.raises(ValueError, match="Unknown aggregate"):
        tracker.aggregate_by("not_a_real_dim")  # type: ignore[arg-type]


def test_noop_returns_tracker_with_empty_buffer():
    t = CostTracker.noop()
    assert isinstance(t._buffer, deque)
    assert t.wal is None
    t.emit(_record())
    assert len(t.records) == 1  # buffer still works, just not persisted


def test_estimate_cost_sanity_known_models():
    """Pricing must be > 0 for known Anthropic models."""
    cost = estimate_cost("claude-sonnet-4-6", 1000, 1000)
    assert cost > 0
    # Sonnet is cheaper than Opus at same token count
    sonnet = estimate_cost("claude-sonnet-4-6", 1000, 1000)
    opus = estimate_cost("claude-opus-4-8", 1000, 1000)
    haiku = estimate_cost("claude-haiku-4-5", 1000, 1000)
    assert haiku < sonnet < opus


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost("totally-unknown-model", 1000, 1000) == 0.0


def test_make_record_estimates_cost_from_tokens():
    rec = make_record(
        model="claude-sonnet-4-6",
        hint=ModelHint.PLANNER,
        role="implementer",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    assert rec.cost_usd > 0
    assert rec.prompt_tokens == 1000
    assert rec.completion_tokens == 500


def test_pricing_table_has_anthropic_models():
    """Sanity check the pricing table covers all v1.2 models."""
    assert "claude-haiku-4-5" in PRICING_PER_1K_TOKENS
    assert "claude-sonnet-4-6" in PRICING_PER_1K_TOKENS
    assert "claude-opus-4-8" in PRICING_PER_1K_TOKENS