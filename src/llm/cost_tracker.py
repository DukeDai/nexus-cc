"""Cost tracking for Model Router.

CostTracker buffers CostRecord events in-process with optional WAL append for
post-mortem cost analysis. The aggregator is intentionally simple: it returns
plain dicts keyed by a dimension so callers can serialize to JSON or pretty-print.

Pricing table is hardcoded with rough Anthropic list prices; actual rates can
be tuned later via env vars or a `pricing.yaml` (out of scope for v1.2).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from src.llm.model_policy import ModelHint

if TYPE_CHECKING:
    from src.context.wal import WALManager

logger = logging.getLogger(__name__)

Dimension = Literal["model", "hint", "role", "session"]

# Rough Anthropic list prices (USD per 1K tokens). Tune later.
PRICING_PER_1K_TOKENS: dict[str, tuple[float, float]] = {
    # model_name: (input_cost, output_cost)
    "claude-haiku-4-5": (0.00080, 0.00400),
    "claude-sonnet-4-6": (0.00300, 0.01500),
    "claude-opus-4-8": (0.01500, 0.07500),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single request. Unknown models cost $0 (logged)."""
    rates = PRICING_PER_1K_TOKENS.get(model)
    if rates is None:
        logger.debug("No pricing for model %s; reporting 0 cost", model)
        return 0.0
    in_rate, out_rate = rates
    return (prompt_tokens / 1000.0) * in_rate + (completion_tokens / 1000.0) * out_rate


@dataclass
class CostRecord:
    """One LLM call's cost event."""

    model: str
    hint: ModelHint
    role: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hint"] = self.hint.value
        return d


@dataclass
class CostTracker:
    """In-memory ring buffer of CostRecords, with optional WAL append.

    Args:
        project_root: Used as anchor for default WAL location when wal=None.
        wal: Optional WALManager. If provided, records are appended via wal.append_cost().
        buffer_size: Max records to retain before oldest are dropped.
    """

    project_root: Path
    wal: Any | None = None  # avoid hard import on WALManager
    buffer_size: int = 10000
    _buffer: deque = field(default_factory=deque)

    def emit(self, record: CostRecord) -> None:
        """Buffer the record and, if WAL is configured, append there too."""
        # Lazy-resize buffer if buffer_size differs from initial (allows
        # post-init mutation while preserving ring-buffer semantics).
        if self._buffer.maxlen != self.buffer_size:
            self._buffer = deque(self._buffer, maxlen=self.buffer_size)
        self._buffer.append(record)
        if self.wal is not None and hasattr(self.wal, "append_cost"):
            try:
                self.wal.append_cost(record.to_dict())
            except Exception as exc:  # WAL failures must not break LLM calls
                logger.warning("WAL append_cost failed: %s", exc)

    def aggregate_by(self, dimension: Dimension) -> dict[str, dict[str, float]]:
        """Aggregate totals by a dimension. Returns {dimension_value: {metric: total}}.

        Metrics: prompt_tokens, completion_tokens, cost_usd, count.
        """
        valid = {"model", "hint", "role", "session"}
        if dimension not in valid:
            raise ValueError(f"Unknown aggregate dimension: {dimension!r}")
        out: dict[str, dict[str, float]] = {}
        for rec in self._buffer:
            if dimension == "model":
                key = rec.model
            elif dimension == "hint":
                key = rec.hint.value
            elif dimension == "role":
                key = rec.role or "<none>"
            else:  # session
                # bucket by minute for coarse "session" grouping
                key = str(int(rec.timestamp // 60))
            bucket = out.setdefault(key, {
                "prompt_tokens": 0.0,
                "completion_tokens": 0.0,
                "cost_usd": 0.0,
                "count": 0.0,
            })
            bucket["prompt_tokens"] += rec.prompt_tokens
            bucket["completion_tokens"] += rec.completion_tokens
            bucket["cost_usd"] += rec.cost_usd
            bucket["count"] += 1
        return out

    @property
    def records(self) -> list[CostRecord]:
        """Snapshot of the current buffer (oldest-first)."""
        return list(self._buffer)

    @classmethod
    def noop(cls) -> "CostTracker":
        """Return a tracker that discards records — for dry-run / tests."""
        return cls(project_root=Path("."), wal=None, buffer_size=1)


def make_record(
    *,
    model: str,
    hint: ModelHint,
    role: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    role_name: str | None = None,
) -> CostRecord:
    """Convenience builder — estimates cost from token counts."""
    return CostRecord(
        model=model,
        hint=hint,
        role=role,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=estimate_cost(model, prompt_tokens, completion_tokens),
        timestamp=time.time(),
    )