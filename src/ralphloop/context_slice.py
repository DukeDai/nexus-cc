"""ContextSlice — Phase-private context with selective visibility.

Implements HARD isolation between RalphLoop phases:
- Each phase has its own private message slice
- Cross-phase communication ONLY through summaries
- No raw data leaks between phases

Core principle: A phase's LLM context contains ONLY:
1. Its own private slice of messages
2. White-listed injections (compressed summaries from other phases)
3. Decision context (from DecisionContextBuilder)

This is NOT the same as PhaseContext in phase_isolation.py:
- PhaseContext: manages compression of old phases
- ContextSlice: manages visibility enforcement between phases
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from threading import Lock
from typing import Optional

from .phase_isolation import PhaseContext


class BudgetExceededError(Exception):
    """Raised when a phase exceeds its context budget quota."""


class VisibilityRule(Enum):
    """Who can see what in a ContextSlice."""
    PRIVATE = auto()      # Only the owning phase
    INJECTED = auto()     # Explicitly injected into another phase
    SUMMARY = auto()       # Only compressed summary visible


@dataclass
class SliceMessage:
    """A message with visibility metadata."""
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    visibility: VisibilityRule = VisibilityRule.PRIVATE
    source_phase: Optional[str] = None  # Which phase created this


@dataclass
class ContextSlice:
    """Phase-private context slice.

    Each phase gets its own slice. Messages in a slice are NOT
    visible to other phases unless explicitly injected as summary.

    Usage:
        slice_manager = ContextSliceManager()
        slice_manager.enter_phase("ACT")
        slice_manager.add_message("assistant", "Implementing...")
        # ACT's LLM only sees ACT's messages
        # To see PLAN's work: slice_manager.inject_phase_summary("PLAN", "ACT")
    """
    phase: str
    messages: list[SliceMessage] = field(default_factory=list)
    injected_summaries: list[str] = field(default_factory=list)
    visibility_rules: dict[str, VisibilityRule] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add_message(self, role: str, content: str) -> None:
        """Add a private message to this slice."""
        with self._lock:
            self.messages.append(SliceMessage(
                role=role,
                content=content,
                visibility=VisibilityRule.PRIVATE,
                source_phase=self.phase,
            ))

    def inject_summary(self, source_phase: str, summary: str) -> None:
        """Inject a compressed summary from another phase.

        This is how phases communicate — only summaries, never raw data.
        """
        with self._lock:
            self.injected_summaries.append(f"[{source_phase}] {summary}")
            self.messages.append(SliceMessage(
                role="system",
                content=f"[Context from {source_phase}]: {summary}",
                visibility=VisibilityRule.INJECTED,
                source_phase=source_phase,
            ))

    def get_llm_messages(self) -> list[dict]:
        """Get messages formatted for LLM consumption.

        Returns only this phase's private messages + injected summaries.
        NO raw data from other phases.
        """
        with self._lock:
            result = []
            for msg in self.messages:
                if msg.visibility == VisibilityRule.PRIVATE:
                    result.append({"role": msg.role, "content": msg.content})
                elif msg.visibility == VisibilityRule.INJECTED:
                    result.append({"role": msg.role, "content": msg.content})
            return result

    def is_private(self) -> bool:
        """Check if slice has no injected content."""
        return all(
            m.visibility == VisibilityRule.PRIVATE
            for m in self.messages
        )

    def clear_injections(self) -> None:
        """Remove all injected summaries (when leaving phase)."""
        with self._lock:
            self.messages = [
                m for m in self.messages
                if m.visibility == VisibilityRule.PRIVATE
            ]
            self.injected_summaries.clear()


@dataclass
class InjectionPolicy:
    """Defines what one phase can inject into another."""
    keep_policy: str = "summary"  # "full" | "summary" | "diff" | "error_summary"
    max_age_seconds: float = 300.0  # Don't inject stale summaries
    inject_conditions: dict[str, dict] = field(default_factory=dict)  # target_phase -> condition rules


# Per-phase compression policies with conditional injection
COMPRESS_POLICY: dict[str, dict] = {
    "PLAN": {
        "threshold": 0.50,
        "keep": "full",  # Planning needs context
        "inject_into": ["ACT", "VERIFY"],
        "inject_conditions": {
            "ACT": {
                "on_error": True,      # Inject on error
                "on_budget_low": False,  # Don't inject when budget is low
            },
            "VERIFY": {
                "on_error": True,
                "on_budget_low": True,  # VERIFY needs context even when budget is low
            },
        },
    },
    "ACT": {
        "threshold": 0.60,
        "keep": "diff",  # Execution cares about results
        "inject_into": ["VERIFY", "REFLECT"],
        "inject_conditions": {
            "VERIFY": {
                "on_error": True,
                "on_success": True,   # Inject results on success too
                "on_budget_low": False,
            },
            "REFLECT": {
                "on_error": True,
                "on_budget_low": True,  # REFLECT needs error context
            },
        },
    },
    "VERIFY": {
        "threshold": 0.40,
        "keep": "summary",  # Verification can be aggressive
        "inject_into": ["REFLECT"],
        "inject_conditions": {
            "REFLECT": {
                "on_error": True,
                "on_budget_low": True,
            },
        },
    },
    "REFLECT": {
        "threshold": 0.55,
        "keep": "error_summary",
        "inject_into": ["PLAN"],  # Next iteration
        "inject_conditions": {
            "PLAN": {
                "on_error": True,
                "on_success": True,   # Always inject to next PLAN
                "on_budget_low": False,
            },
        },
    },
    "DECOMPOSE": {
        "threshold": 0.45,
        "keep": "full",
        "inject_into": ["PLAN"],
        "inject_conditions": {
            "PLAN": {
                "on_error": False,
                "on_success": True,
                "on_budget_low": False,
            },
        },
    },
}


# Phase-specific budget thresholds for injection decisions
# Maps phase -> threshold above which budget is considered "low"
_BUDGET_THRESHOLDS: dict[str, float] = {
    "PLAN": 75.0,
    "DECOMPOSE": 70.0,
    "ACT": 70.0,
    "VERIFY": 60.0,  # VERIFY is more conservative
    "REFLECT": 80.0,
}


class InjectionCondition:
    """Conditions that control when injection happens."""

    @staticmethod
    def should_inject(
        source_phase: str,
        target_phase: str,
        context: dict,
    ) -> bool:
        """Determine if injection should happen based on conditions.

        Args:
            source_phase: Phase doing the injection
            target_phase: Phase receiving the injection
            context: Current execution context with 'last_phase_error', 'context_budget', etc.

        Returns:
            True if injection should happen
        """
        if source_phase not in COMPRESS_POLICY:
            return True  # Allow if no policy defined

        policy = COMPRESS_POLICY[source_phase]
        conditions = policy.get("inject_conditions", {}).get(target_phase, {})

        if not conditions:
            return True  # No conditions = always inject

        # Check each condition
        if conditions.get("on_error") and context.get("last_phase_error"):
            return True
        if conditions.get("on_success") and context.get("last_phase_success"):
            return True
        if conditions.get("on_budget_low"):
            threshold = _BUDGET_THRESHOLDS.get(target_phase, 70.0)
            if context.get("context_budget", 100) >= threshold:
                return True  # Above threshold = budget running low

        # Default: don't inject if conditions exist but none met
        return False


class ContextSliceManager:
    """Manages ContextSlice instances with visibility enforcement.

    Key features:
    - Each phase gets isolated message storage
    - Cross-phase communication ONLY via summaries
    - Compression policy per phase type
    - Thread-safe operations
    - Conditional injection based on error/success/budget state
    - Per-phase budget quotas for differentiated resource allocation

    Usage:
        manager = ContextSliceManager()
        manager.enter_phase("PLAN")
        manager.add_message("user", "Build a web server")
        # ... planning happens ...
        manager.exit_phase("PLAN")

        manager.enter_phase("ACT")
        # ACT sees ONLY its own messages initially
        # To see PLAN summary:
        if manager.can_inject("PLAN", "ACT"):
            manager.inject_phase_summary("PLAN", "ACT")

        # Conditional injection based on execution context:
        ctx = {"last_phase_error": False, "last_phase_success": True, "context_budget": 45}
        manager.inject_with_conditions("PLAN", "ACT", ctx)
    """

    # Per-phase budget quotas (% of total context budget)
    PHASE_BUDGET_QUOTAS: dict[str, float] = {
        "PLAN": 0.25,      # 25% for planning
        "DECOMPOSE": 0.15, # 15% for decomposition
        "ACT": 0.35,       # 35% for execution (most expensive)
        "VERIFY": 0.15,    # 15% for verification
        "REFLECT": 0.10,   # 10% for reflection
    }

    def __init__(self, context_window: int = 100000):
        self.context_window = context_window
        self.slices: dict[str, ContextSlice] = {}
        self._current_phase: Optional[str] = None
        self._lock = Lock()
        self._phase_summaries: dict[str, str] = {}  # phase -> summary
        self._phase_budget_used: dict[str, float] = {}  # phase -> % used

    def enter_phase(self, phase: str) -> ContextSlice:
        """Enter a phase, creating/getting its slice."""
        with self._lock:
            if phase not in self.slices:
                self.slices[phase] = ContextSlice(phase=phase)
            self._current_phase = phase
            return self.slices[phase]

    def exit_phase(self, phase: str) -> None:
        """Exit a phase, saving its summary for injection."""
        with self._lock:
            if phase in self.slices:
                # Generate and store summary before clearing
                summary = self._generate_slice_summary(self.slices[phase])
                self._phase_summaries[phase] = summary
                # Clear injections when exiting to prevent leak
                self.slices[phase].clear_injections()
            self._current_phase = None

    def get_current_slice(self) -> Optional[ContextSlice]:
        """Get the current active slice."""
        if self._current_phase:
            return self.slices.get(self._current_phase)
        return None

    def add_message(self, role: str, content: str) -> None:
        """Add message to current phase slice."""
        slice = self.get_current_slice()
        if slice:
            slice.add_message(role, content)

    def get_llm_messages(self, phase: str, max_messages: int | None = None) -> list[dict]:
        """Get LLM-ready messages for a phase.

        This is the SOLE entry point for getting messages to the LLM.
        Enforces visibility: no raw data from other phases.
        Applies budget cap to prevent unbounded context growth.

        Args:
            phase: Phase to get messages for
            max_messages: Optional cap on message count to prevent budget overflow.
                          If None, uses per-phase budget quota to determine limit.
        """
        slice = self.slices.get(phase)
        if not slice:
            return []
        messages = slice.get_llm_messages()

        # Apply budget cap if limit specified or can be computed from quota
        if max_messages is None:
            quota = self.PHASE_BUDGET_QUOTAS.get(phase, 0.10)
            # Estimate max messages from quota: assume ~500 tokens per message
            # 10% of 200k context = 20k tokens, / 500 = ~40 messages
            max_messages = int(quota * self.context_window / 500)

        if len(messages) > max_messages:
            # Keep critical messages (decisions, errors), truncate less important
            critical = []
            non_critical = []
            for m in messages:
                content_lower = m.get("content", "").lower()
                if any(kw in content_lower for kw in ["decision", "error", "fail", "exception", "critical"]):
                    critical.append(m)
                else:
                    non_critical.append(m)
            # Prioritize keeping critical, trim non-critical from the back
            available = max_messages - len(critical)
            if available > 0:
                messages = critical + non_critical[-available:]
            else:
                messages = critical[:max_messages]

        return messages

    def can_inject(self, source_phase: str, target_phase: str) -> bool:
        """Check if source_phase can inject into target_phase."""
        if source_phase not in COMPRESS_POLICY:
            return False
        policy = COMPRESS_POLICY[source_phase]
        return target_phase in policy.get("inject_into", [])

    def inject_phase_summary(
        self,
        source_phase: str,
        target_phase: str,
        execution_context: dict | None = None,
    ) -> bool:
        """Inject source_phase's summary into target_phase.

        Returns True if injection happened.

        Args:
            source_phase: Phase doing the injection
            target_phase: Phase receiving the injection
            execution_context: Optional dict with 'last_phase_error', 'last_phase_success',
                              'context_budget' for conditional injection
        """
        if not self.can_inject(source_phase, target_phase):
            return False

        # Check conditional injection policy if context provided
        if execution_context is not None:
            if not self.should_inject(source_phase, target_phase, execution_context):
                return False

        summary = self._phase_summaries.get(source_phase)
        if not summary:
            # Generate from slice if no stored summary
            if source_phase in self.slices:
                summary = self._generate_slice_summary(self.slices[source_phase])

        if summary and target_phase in self.slices:
            self.slices[target_phase].inject_summary(source_phase, summary)
            return True
        return False

    def auto_inject_allowed(self, source_phase: str, target_phase: str) -> bool:
        """Check if auto-injection policy allows this injection."""
        policy = COMPRESS_POLICY.get(source_phase, {})
        return target_phase in policy.get("inject_into", [])

    def should_inject(self, source_phase: str, target_phase: str, context: dict) -> bool:
        """Check if injection should happen given current execution context.

        Uses conditional injection rules to decide whether to inject,
        based on error state, success state, and context budget.
        """
        return InjectionCondition.should_inject(source_phase, target_phase, context)

    def inject_with_conditions(
        self,
        source_phase: str,
        target_phase: str,
        execution_context: dict,
    ) -> bool:
        """Conditionally inject based on execution context.

        Args:
            source_phase: Phase doing the injection
            target_phase: Phase receiving the injection
            execution_context: dict with keys like:
                - last_phase_error: bool
                - last_phase_success: bool
                - context_budget: float (0-100)

        Returns:
            True if injection happened, False if skipped due to conditions
        """
        if not self.should_inject(source_phase, target_phase, execution_context):
            return False
        return self.inject_phase_summary(source_phase, target_phase)

    def _generate_slice_summary(self, slice: ContextSlice) -> str:
        """Generate a compressed summary of a slice's work."""
        msg_count = len(slice.messages)
        injected_count = len(slice.injected_summaries)

        lines = [f"Phase: {slice.phase}, Messages: {msg_count}, Injections: {injected_count}"]

        # Summarize tool results (if any were recorded as content)
        tool_results = [
            m.content for m in slice.messages
            if "tool" in m.content.lower()[:50]  # Quick heuristic
        ]
        if tool_results:
            lines.append(f"Tool activity: {len(tool_results)} tool-related messages")

        return " | ".join(lines)

    def get_slice_stats(self, phase: str) -> dict:
        """Get statistics for a slice."""
        slice = self.slices.get(phase)
        if not slice:
            return {}
        return {
            "phase": phase,
            "message_count": len(slice.messages),
            "injection_count": len(slice.injected_summaries),
            "is_private": slice.is_private(),
            "has_summary": phase in self._phase_summaries,
        }

    def clear_all(self) -> None:
        """Clear all slices and summaries."""
        with self._lock:
            self.slices.clear()
            self._phase_summaries.clear()
            self._current_phase = None

    def estimate_slice_budget(self, phase: str) -> float:
        """Estimate token usage for a slice."""
        slice = self.slices.get(phase)
        if not slice:
            return 0.0

        total_chars = sum(len(m.content) for m in slice.messages)
        estimated_tokens = total_chars / 4
        return (estimated_tokens / self.context_window) * 100

    def get_phase_budget_remaining(self, phase: str) -> float:
        """Get remaining budget quota for a phase.

        Returns the % of total context budget remaining for this phase,
        accounting for its quota allocation.

        Raises:
            BudgetExceededError: If phase has exceeded its quota.
        """
        quota = self.PHASE_BUDGET_QUOTAS.get(phase, 0.20)  # 20% default
        used = self._phase_budget_used.get(phase, 0.0)
        remaining = max(0.0, quota - used)
        if remaining <= 0:
            raise BudgetExceededError(
                f"Phase '{phase}' exceeded budget quota: used={used:.1%}, quota={quota:.1%}"
            )
        return remaining

    def record_phase_budget_usage(self, phase: str, used_percent: float) -> None:
        """Record how much budget a phase used this turn."""
        self._phase_budget_used[phase] = used_percent

    def get_total_budget_usage(self) -> float:
        """Get total estimated budget usage across all phases."""
        total = 0.0
        for slice in self.slices.values():
            total_chars = sum(len(m.content) for m in slice.messages)
            estimated_tokens = total_chars / 4
            total += (estimated_tokens / self.context_window) * 100
        return total