"""Phase Isolation — Context Partitioning and Compression.

This module implements phase-isolated context management:
- Each RalphLoop phase (PLAN/ACT/VERIFY/REFLECT) gets its own context
- Failed trajectories are stored separately, never pollute LLM context
- Old phases are compressed into summaries when memory pressure increases
- Semantic-aware compression preserves decisions, error lessons, dependencies

Key principle: The LLM only receives what it needs, when it needs it.
Details are summarized; only the decision-relevant summary is injected.

Semantic Compression:
    Instead of threshold-based compression, uses SemanticChunker to:
    - Preserve: key decisions, error lessons, dependencies
    - Discard: mechanical repetition, redundant debug output
    - Build summaries that maintain decision context
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from threading import Lock

from .semantic_chunker import SemanticChunker, CompressionDecision, ChunkType


@dataclass
class PhaseContext:
    """Isolated context for a single RalphLoop phase.

    Each phase accumulates its own messages and tool results.
    When compressed, only the summary survives.
    """
    phase: str
    messages: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    compressed: bool = False

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def add_tool_result(self, tool: str, output: str, success: bool = True) -> None:
        self.tool_results.append({
            "tool": tool,
            "output": output[:500] if len(output) > 500 else output,  # Truncate
            "success": success,
            "timestamp": datetime.now().isoformat()
        })

    def compress(self, summary: str) -> None:
        """Compress this phase — keep summary, discard details."""
        self.summary = summary
        self.messages.clear()
        self.tool_results.clear()
        self.compressed = True

    def is_empty(self) -> bool:
        return len(self.messages) == 0 and len(self.tool_results) == 0


@dataclass
class CompressionResult:
    """Result of context compression."""
    original_size: int  # Estimated tokens before compression
    compressed_size: int  # Estimated tokens after compression
    compression_ratio: float
    phases_compressed: list[str]
    summary: str  # LLM-readable summary


# Phase-specific summarizer registry
# Maps phase name -> summarizer method on IsolatedContextManager
PHASE_SUMMARIZERS: dict[str, callable] = {
    "PLAN": lambda ctx: IsolatedContextManager._summarize_plan(ctx),
    "ACT": lambda ctx: IsolatedContextManager._summarize_act(ctx),
    "VERIFY": lambda ctx: IsolatedContextManager._summarize_verify(ctx),
    "REFLECT": lambda ctx: IsolatedContextManager._summarize_reflect(ctx),
    "DECOMPOSE": lambda ctx: IsolatedContextManager._summarize_decompose(ctx),
}


class IsolatedContextManager:
    """Manages phase-isolated contexts with compression.

    Key features:
    - Phase isolation: each RalphLoop state has its own context
    - Compression: old phases summarized, details evicted
    - Selective injection: LLM gets summaries, not full traces
    - Budget-aware: triggers compression at threshold
    - Phase-specific summarizers: each phase type gets tailored summary

    Usage:
        manager = IsolatedContextManager(context_window=100000)
        ctx = manager.enter_phase(RalphState.PLAN)
        ctx.add_message("user", "task description")
        # ... work happens ...
        manager.compress_if_needed()
        decision_context = manager.get_decision_context()  # For orchestrator
    """

    # Compression triggers at these budget percentages
    COMPRESS_AT_DEGRADING = 50.0   # 50% context → compress old phases
    EVICT_AT_POOR = 70.0           # 70% → aggressive eviction

    def __init__(self, context_window: int = 100000):
        self.context_window = context_window
        self.phase_contexts: dict[str, PhaseContext] = {}
        self.summary_history: list[str] = []  # All phase summaries
        self._current_phase: Optional[str] = None
        self._lock = Lock()  # Thread-safe mutations
        self._semantic_chunker = SemanticChunker()  # NEW: semantic chunker

    def enter_phase(self, phase: str) -> PhaseContext:
        """Enter a new phase, creating or retrieving its context."""
        with self._lock:
            if phase not in self.phase_contexts:
                self.phase_contexts[phase] = PhaseContext(phase=phase)
            ctx = self.phase_contexts[phase]
            ctx.compressed = False  # Reactivate if was compressed
            self._current_phase = phase
            return ctx

    def get_current_phase(self) -> Optional[PhaseContext]:
        """Get the current active phase context."""
        if self._current_phase:
            return self.phase_contexts.get(self._current_phase)
        return None

    def exit_phase(self, phase: str) -> None:
        """Mark a phase as complete (doesn't delete its context)."""
        self._current_phase = None

    def estimate_budget(self) -> float:
        """Estimate current context usage as percentage."""
        total_chars = 0
        for ctx in self.phase_contexts.values():
            for msg in ctx.messages:
                total_chars += len(msg.get("content", ""))
            for tr in ctx.tool_results:
                total_chars += len(tr.get("output", ""))

        estimated_tokens = total_chars / 4  # Rough estimate
        return (estimated_tokens / self.context_window) * 100

    def should_compress(self) -> bool:
        """Check if compression should be triggered."""
        return self.estimate_budget() >= self.COMPRESS_AT_DEGRADING

    def should_evict(self) -> bool:
        """Check if aggressive eviction should happen."""
        return self.estimate_budget() >= self.EVICT_AT_POOR

    def _semantic_compress_phase(self, ctx: PhaseContext) -> str:
        """Compress a phase using semantic chunking.

        Instead of simple truncation, preserves:
        - Key decisions and alternatives
        - Error lessons
        - Dependencies
        - Critical context

        Discards:
        - Mechanical repetition
        - Redundant debug output
        - Routine operations
        """
        # Convert messages to semantic chunks
        chunks = self._semantic_chunker.chunk_messages(ctx.messages)

        if not chunks:
            return ctx.summary or f"{ctx.phase}: {len(ctx.messages)} messages"

        # Compress based on semantic importance
        budget = self.estimate_budget()
        compression = self._semantic_chunker.compress(
            chunks,
            budget_percent=budget,
            target_ratio=0.5
        )

        return compression.summary if compression.summary else ctx.summary or ""

    def compress_if_needed(self) -> Optional[CompressionResult]:
        """Compress old phases if budget threshold exceeded.

        Returns CompressionResult if compression happened, None otherwise.
        """
        if not self.should_compress():
            return None

        current_phase = self._current_phase
        phases_to_compress = [
            p for p in self.phase_contexts
            if p != current_phase and not self.phase_contexts[p].compressed
        ]

        if not phases_to_compress:
            return None

        # Compress oldest first (skip current)
        compressed_phases = []
        original_total = 0
        compressed_total = 0

        for phase in sorted(phases_to_compress, key=lambda p: self.phase_contexts[p].created_at):
            ctx = self.phase_contexts[phase]
            if ctx.is_empty():
                continue

            # Generate summary using semantic compression
            summary = self._semantic_compress_phase(ctx)
            ctx.compress(summary)

            self.summary_history.append(f"[{phase}] {summary}")

            # Track sizes
            for msg in ctx.messages:
                original_total += len(msg.get("content", ""))
            compressed_total += len(summary)

            compressed_phases.append(phase)

            # Stop if we've compressed enough
            if self.estimate_budget() < self.COMPRESS_AT_DEGRADING:
                break

        if not compressed_phases:
            return None

        return CompressionResult(
            original_size=original_total // 4,  # Rough token estimate
            compressed_size=compressed_total // 4,
            compression_ratio=compressed_total / max(original_total, 1),
            phases_compressed=compressed_phases,
            summary="\n".join(self.summary_history[-5:])
        )

    def _summarize_phase(self, ctx: PhaseContext) -> str:
        """Generate a summary of a phase's work using phase-specific strategy.

        This is what the LLM receives — not the full context.
        Phase-specific summarizers preserve the information each consumer needs.
        """
        method_name = f"_summarize_{ctx.phase.lower()}"
        summarizer = getattr(self, method_name, self._summarize_generic)
        return summarizer(ctx)

    def _summarize_generic(self, ctx: PhaseContext) -> str:
        """Default summarizer for unknown phases."""
        lines = [f"Phase: {ctx.phase}"]

        if ctx.tool_results:
            successes = [t for t in ctx.tool_results if t.get("success")]
            failures = [t for t in ctx.tool_results if not t.get("success")]
            lines.append(f"Tools: {len(successes)} succeeded, {len(failures)} failed")
            if failures:
                error_types = set()
                for f in failures:
                    err = f.get("output", "")
                    if "ERROR" in err:
                        error_types.add(err.split("ERROR")[1][:50].strip())
                lines.append(f"Errors: {', '.join(error_types) if error_types else 'see logs'}")

        if ctx.messages:
            total_chars = sum(len(m.get("content", "")) for m in ctx.messages)
            lines.append(f"Messages: {len(ctx.messages)} (~{total_chars//4} tokens)")

        return " | ".join(lines)

    def _summarize_plan(self, ctx: PhaseContext) -> str:
        """Summarize PLAN phase — preserve decision path and planned steps."""
        lines = [f"PLAN: {len(ctx.messages)} messages"]

        # Extract planned tasks/goals if present in messages
        goals = []
        for msg in ctx.messages[-5:]:  # Last 5 messages most relevant
            content = msg.get("content", "")
            if "plan" in content.lower()[:50] or "step" in content.lower()[:50]:
                goals.append(content[:100])
        if goals:
            lines.append(f"Goals: {'; '.join(goals[:3])}")

        # Tool results show planning artifacts created
        if ctx.tool_results:
            artifacts = [t.get("output", "")[:80] for t in ctx.tool_results if t.get("success")]
            if artifacts:
                lines.append(f"Artifacts: {len(artifacts)} created")

        return " | ".join(lines)

    def _summarize_act(self, ctx: PhaseContext) -> str:
        """Summarize ACT phase — preserve diffs and results, not execution trace."""
        lines = [f"ACT: {len(ctx.messages)} messages"]

        if ctx.tool_results:
            successes = [t for t in ctx.tool_results if t.get("success")]
            failures = [t for t in ctx.tool_results if not t.get("success")]
            lines.append(f"Tools: {len(successes)} OK, {len(failures)} failed")

            # Capture key outputs without full trace
            if successes:
                # Last success is most relevant
                last_success = successes[-1].get("output", "")[:150]
                lines.append(f"Last result: {last_success}")

            if failures:
                # Just the error type, not full trace
                error_types = [f.get("output", "")[:80].split("ERROR")[-1].strip() for f in failures]
                lines.append(f"Failures: {', '.join(set(error_types[:3]))}")

        return " | ".join(lines)

    def _summarize_verify(self, ctx: PhaseContext) -> str:
        """Summarize VERIFY phase — only assertion results, not execution details."""
        lines = [f"VERIFY: {len(ctx.messages)} messages"]

        if ctx.tool_results:
            # VERIFY only cares about pass/fail
            passes = [t for t in ctx.tool_results if t.get("success")]
            fails = [t for t in ctx.tool_results if not t.get("success")]
            lines.append(f"Assertions: {len(passes)} passed, {len(fails)} failed")

            if fails:
                # Just the assertion that failed, not the full test output
                failed_assertions = []
                for f in fails:
                    output = f.get("output", "")
                    # Extract just the assertion message
                    if "assert" in output.lower():
                        failed_assertions.append(output[:100].split("\n")[0])
                    else:
                        failed_assertions.append(output[:80])
                lines.append(f"Failed: {'; '.join(failed_assertions[:2])}")

        return " | ".join(lines)

    def _summarize_reflect(self, ctx: PhaseContext) -> str:
        """Summarize REFLECT phase — preserve error patterns and learnings, not full trace."""
        lines = [f"REFLECT: {len(ctx.messages)} messages"]

        # Extract key learnings from reflection
        if ctx.messages:
            # Look for patterns in assistant messages
            patterns = []
            for msg in ctx.messages:
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if "learned" in content.lower()[:50] or "pattern" in content.lower()[:50]:
                        patterns.append(content[:120])
            if patterns:
                lines.append(f"Learnings: {'; '.join(patterns[:2])}")

        # Error summary from tool results
        if ctx.tool_results:
            errors = [t.get("output", "")[:80] for t in ctx.tool_results if not t.get("success")]
            if errors:
                lines.append(f"Error patterns: {len(errors)} identified")

        return " | ".join(lines)

    def _summarize_decompose(self, ctx: PhaseContext) -> str:
        """Summarize DECOMPOSE phase — preserve task breakdown structure."""
        lines = [f"DECOMPOSE: {len(ctx.messages)} messages"]

        if ctx.tool_results:
            subtasks = []
            for t in ctx.tool_results:
                if t.get("success"):
                    output = t.get("output", "")
                    # Extract task descriptions
                    if "task" in output.lower()[:50]:
                        subtasks.append(output[:80].split("\n")[0])
            if subtasks:
                lines.append(f"Subtasks: {len(subtasks)} created")
                lines.append(f"First: {subtasks[0]}")

        return " | ".join(lines)

    def get_decision_context(self) -> str:
        """Return context for orchestrator decision-making.

        Strategy: Most recent phase details + all phase summaries.
        Never full error traces in LLM context.
        """
        parts = []

        # Recent phase summaries
        if self.summary_history:
            parts.append("=== Phase History ===")
            parts.append("\n".join(self.summary_history[-5:]))

        # Current phase details
        current = self.get_current_phase()
        if current and not current.is_empty():
            parts.append(f"\n=== Current Phase: {current.phase} ===")
            if current.tool_results:
                parts.append(f"Recent tools: {len(current.tool_results)}")
            if current.messages:
                last_msg = current.messages[-1]
                parts.append(f"Last: {last_msg.get('role')}: {last_msg.get('content', '')[:200]}")

        return "\n".join(parts) if parts else "No context available."

    def get_messages_for_llm(self, phase: str) -> list[dict]:
        """Get messages for LLM consumption in a specific phase.

        Returns only the current phase's messages OR summary if compressed.
        """
        ctx = self.phase_contexts.get(phase)
        if not ctx:
            return []

        if ctx.compressed:
            # Return a summary message instead of full history
            return [{
                "role": "system",
                "content": f"Previous work summary: {ctx.summary}"
            }]

        return ctx.messages.copy()

    def inject_summary(self, target_phase: str) -> str:
        """Inject compressed summary for a phase into current context.

        Use this when you need to reference a previous phase's work.
        """
        summaries = [
            f"[{ctx.phase}] {ctx.summary}"
            for ctx in self.phase_contexts.values()
            if ctx.summary
        ]
        return "\n".join(summaries[-3:])  # Last 3 phase summaries

    def clear_all(self) -> None:
        """Clear all phase contexts."""
        self.phase_contexts.clear()
        self.summary_history.clear()
        self._current_phase = None

    def get_stats(self) -> dict:
        """Get context management statistics."""
        active = [p for p, ctx in self.phase_contexts.items() if not ctx.compressed]
        compressed = [p for p, ctx in self.phase_contexts.items() if ctx.compressed]
        return {
            "total_phases": len(self.phase_contexts),
            "active_phases": len(active),
            "compressed_phases": len(compressed),
            "current_phase": self._current_phase,
            "budget_percent": round(self.estimate_budget(), 1),
            "summary_count": len(self.summary_history)
        }