"""Error Isolation — Failed Trajectory Management.

This module implements the core principle that FAILED TRAJECTORIES MUST NOT
pollute the context used for decision-making. The main task only receives:
1. Final result (success/failure)
2. Causal graph with root causes (not full trace)
3. Actionable recommendations (not full trace)
4. Phase completion status

Key invariant: Error traces stay isolated; only causal summaries reach the LLM.

Causal Error Analysis:
    Instead of static recovery_hint, uses CausalErrorAnalyzer to build
    actual causal chains from failed trajectories. This distinguishes:
    - "Permission denied" after timeout (different root cause)
    - "Permission denied" in isolation (real permission issue)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional

from .causal_error_graph import (
    CausalErrorAnalyzer,
    CausalAnalysisResult,
    ActionableRecommendation,
)


class ErrorCategory(Enum):
    """Error classification for targeted recovery strategies.

    Different error types require different handling:
    - TIMEOUT: May succeed on retry with extended timeout
    - ASSERTION_FAILED: Logic bug or spec misunderstanding, retry unlikely to help
    - TOOL_CALL_FAILED: External tool issue, may need alternative tool
    - PERMISSION_DENIED: Auth/access issue, retries won't help
    - RESOURCE_EXHAUSTED: Budget/memory limits, need to decompose task
    - UNKNOWN: Needs investigation
    """
    TIMEOUT = "timeout"
    ASSERTION_FAILED = "assertion_failed"
    TOOL_CALL_FAILED = "tool_call_failed"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    UNKNOWN = "unknown"


# Strategy mapping: error category -> (retries, action, base_backoff_ms)
ERROR_STRATEGY_MAP: dict[ErrorCategory, tuple[int, str, int]] = {
    ErrorCategory.PERMISSION_DENIED: (0, "escalate", 0),
    ErrorCategory.RESOURCE_EXHAUSTED: (0, "decompose", 0),
    ErrorCategory.TIMEOUT: (2, "retry_with_timeout_extended", 500),
    ErrorCategory.ASSERTION_FAILED: (1, "retry_then_decompose", 250),
    ErrorCategory.TOOL_CALL_FAILED: (1, "retry_with_alternative", 1000),
    ErrorCategory.UNKNOWN: (1, "retry_then_escalate", 500),
}

# Backoff limits
MAX_BACKOFF_MS = 30000
INITIAL_BACKOFF_MS = 100


def classify_error(error: str, tool_calls: list[dict]) -> ErrorCategory:
    """Classify an error into its category for strategy selection."""
    error_lower = error.lower()

    # Permission keywords
    if any(kw in error_lower for kw in ["permission", "denied", "access denied", "unauthorized", "forbidden"]):
        return ErrorCategory.PERMISSION_DENIED

    # Resource exhaustion
    if any(kw in error_lower for kw in ["memory", "budget", "quota", "rate limit", "exhausted", "timeout", "timed out"]):
        if any(kw in error_lower for kw in ["memory", "budget", "quota", "rate limit", "exhausted"]):
            return ErrorCategory.RESOURCE_EXHAUSTED
        return ErrorCategory.TIMEOUT

    # Assertion failures
    if any(kw in error_lower for kw in ["assert", "expected", "actual", "mismatch", "not equal", "failed check"]):
        return ErrorCategory.ASSERTION_FAILED

    # Tool call failures — only if error references a specific tool name
    if tool_calls:
        tool_names = {tc.get("name", "").lower() for tc in tool_calls}
        if any(name in error_lower for name in tool_names if name):
            return ErrorCategory.TOOL_CALL_FAILED

    return ErrorCategory.UNKNOWN


class ErrorIsolationStrategy(Enum):
    """How to handle failed trajectories in context.

    SHADOW:    Store separately, NEVER in LLM context (default)
    SUMMARY:   Summarize errors, discard details
    EVICT:     Remove failed attempts from context entirely
    """
    SHADOW = auto()
    SUMMARY = auto()
    EVICT = auto()


@dataclass
class FailedTrajectory:
    """A failed attempt with isolation from main context.

    Attributes:
        phase: Which RalphLoop phase this occurred in
        tool_calls: Original tool call sequence that failed
        error: Error message/reason for failure
        causal_analysis: Causal graph with root causes and recommendations
        timestamp: When the failure occurred
        contamination_risk: HIGH/MEDIUM/LOW assessment
        error_category: Classified error type for strategy selection
        strategy_retries: How many retries this error type allows
        strategy_action: Recommended action for this error type
    """
    phase: str
    tool_calls: list[dict]
    error: str
    causal_analysis: Optional[CausalAnalysisResult] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    contamination_risk: str = "HIGH"
    error_category: ErrorCategory = field(default_factory=lambda: ErrorCategory.UNKNOWN)
    strategy_retries: int = field(default=1, init=False)
    strategy_action: str = field(default="retry_then_escalate", init=False)
    _base_backoff_ms: int = field(default=500, init=False)

    @property
    def base_backoff_ms(self) -> int:
        return self._base_backoff_ms

    def compute_backoff(self, attempt: int) -> int:
        """Compute backoff delay for a given retry attempt.

        Uses exponential backoff: min(base_backoff_ms * 2^attempt, MAX_BACKOFF_MS).
        """
        if self._base_backoff_ms == 0:
            return 0
        return min(self._base_backoff_ms * (2 ** attempt), MAX_BACKOFF_MS)

    def __post_init__(self):
        # Run causal analysis to build error chain
        if self.causal_analysis is None:
            analyzer = CausalErrorAnalyzer()
            self.causal_analysis = analyzer.analyze(self.phase, self.tool_calls, self.error)

        # Use causal analysis results
        self.error_category = self._causal_category_to_enum(
            self.causal_analysis.error_category
        )

        strategy = ERROR_STRATEGY_MAP.get(self.error_category, (1, "retry_then_escalate", 500))
        self.strategy_retries = strategy[0]
        self.strategy_action = strategy[1]
        self._base_backoff_ms = strategy[2]

    def _causal_category_to_enum(self, category: str) -> ErrorCategory:
        """Convert causal analysis category string to ErrorCategory enum."""
        mapping = {
            "PERMISSION_DENIED": ErrorCategory.PERMISSION_DENIED,
            "RESOURCE_EXHAUSTED": ErrorCategory.RESOURCE_EXHAUSTED,
            "TIMEOUT": ErrorCategory.TIMEOUT,
        }
        return mapping.get(category, ErrorCategory.UNKNOWN)

    def get_recovery_context(self) -> dict:
        """Get clean recovery context for orchestrator decision-making.

        Main task receives ONLY causal analysis — no full traces.
        """
        if self.causal_analysis:
            return self.causal_analysis.to_clean_context()
        return {"error": self.error[:150], "strategy": self.strategy_action}

    def get_root_causes(self) -> list[str]:
        """Get root cause descriptions from causal analysis."""
        if self.causal_analysis:
            return self.causal_analysis.root_causes
        return [self.error[:80]]

    def get_actionable_recommendations(self) -> list[ActionableRecommendation]:
        """Get actionable recommendations from causal analysis."""
        if self.causal_analysis:
            return self.causal_analysis.recommendations
        return []

    def _generate_hint(self) -> str:
        """Generate LLM-readable hint from causal analysis."""
        if self.causal_analysis:
            root_cause = self.causal_analysis.root_causes[0] if self.causal_analysis.root_causes else "unknown"
            return (
                f"Causal analysis: {root_cause}. "
                f"Strategy: {self.causal_analysis.recovery_strategy}. "
                f"Trace: {self.causal_analysis.causal_trace[:100]}"
            )

        tc_count = len(self.tool_calls)
        error_preview = self.error[:150] if self.error else "unknown"
        action_hint = {
            "escalate": "Escalate to user — retry won't help.",
            "decompose": "Break into smaller subtasks.",
            "retry_with_timeout_extended": "Retry with extended timeout.",
            "retry_then_decompose": "Retry once, then decompose if it fails.",
            "retry_with_alternative": "Retry with alternative approach.",
            "retry_then_escalate": "Retry once, then escalate if it fails.",
        }.get(self.strategy_action, "Consider alternative approach.")

        return (
            f"Failed after {tc_count} tool call(s). "
            f"Error type: {self.error_category.value}. "
            f"Error: {error_preview}. "
            f"Action: {action_hint}"
        )


@dataclass
class ErrorDecisionContext:
    """Clean context for orchestrator decision-making.

    This is what the main loop sees — NOT error traces, but causal graph.
    Used by RalphLoop for retry/abort decisions.
    """
    trajectory_count: int = 0
    recent_failures: int = 0
    should_retry: bool = True
    recovery_hint: Optional[str] = None
    last_error_phase: Optional[str] = None
    last_error_category: ErrorCategory = ErrorCategory.UNKNOWN
    should_escalate: bool = False
    should_decompose: bool = False
    # Causal analysis fields (NEW)
    root_causes: list[str] = field(default_factory=list)
    causal_trace: str = ""
    causal_recommendations: list[dict] = field(default_factory=list)

    @property
    def is_stuck(self) -> bool:
        """Check if trapped in a repeated failure loop."""
        return self.recent_failures >= 3 and not self.should_retry

    def should_retry_with_strategy(self, trajectory: FailedTrajectory) -> bool:
        """Check if this specific error type should be retried."""
        return trajectory.strategy_retries > 0


class ShadowErrorTracker:
    """Track failed trajectories in isolation from main context.

    Core principle: Failed trajectories are stored separately from the
    context that LLM sees. The orchestrator decides "should we retry?"
    based on metrics, not error traces.

    Optionally wired to SelfEvolutionEngine for cross-session learned recovery.

    Usage:
        tracker = ShadowErrorTracker()
        tracker.record_failure(phase="ACT", tool_calls=[...], error="FileNotFound")
        decision = tracker.get_decision_context()  # Clean context for orchestrator

        # With learned recovery integration:
        from self_evolution import SelfEvolutionEngine
        se = SelfEvolutionEngine()
        tracker = ShadowErrorTracker(self_evolution_engine=se)
    """

    def __init__(
        self,
        strategy: ErrorIsolationStrategy = ErrorIsolationStrategy.EVICT,  # Changed default to EVICT for stronger isolation
        max_trajectories: int = 50,
        self_evolution_engine: Optional["SelfEvolutionEngine"] = None,
    ):
        self.strategy = strategy
        self.max_trajectories = max_trajectories
        self._trajectories: list[FailedTrajectory] = []
        self._phase_errors: dict[str, int] = {}  # Count errors per phase
        self._self_evolution_engine = self_evolution_engine

    @property
    def trajectories(self) -> list[FailedTrajectory]:
        """Read-only access to trajectories."""
        return self._trajectories.copy()

    def record_failure(
        self,
        phase: str,
        tool_calls: list[dict],
        error: str,
        context_messages: Optional[list[dict]] = None
    ) -> FailedTrajectory:
        """Record a failed trajectory in isolation.

        Args:
            phase: RalphLoop phase where failure occurred
            tool_calls: The tool call sequence that failed
            error: Error message
            context_messages: If provided, will be evicted from LLM context

        Returns:
            The FailedTrajectory with auto-classified error and strategy
        """
        trajectory = FailedTrajectory(
            phase=phase,
            tool_calls=tool_calls,
            error=error,
            contamination_risk=self._assess_risk(tool_calls, error)
        )
        self._trajectories.append(trajectory)

        # Track per-phase error counts
        self._phase_errors[phase] = self._phase_errors.get(phase, 0) + 1

        # Evict from main context if strategy demands
        if self.strategy == ErrorIsolationStrategy.EVICT and context_messages is not None:
            self._evict_from_messages(context_messages, tool_calls)

        # Trim if exceeding max
        if len(self._trajectories) > self.max_trajectories:
            self._trajectories = self._trajectories[-self.max_trajectories:]

        return trajectory

    def _assess_risk(self, tool_calls: list[dict], error: str) -> str:
        """Assess how contaminating this failure could be."""
        high_risk_keywords = {"git", "rm", "mv", "delete", "drop", "truncate"}
        tool_names = {tc.get("name", "") for tc in tool_calls}

        if tool_names & high_risk_keywords:
            return "HIGH"
        if len(tool_calls) > 5:
            return "MEDIUM"
        return "LOW"

    def _evict_from_messages(
        self,
        messages: list[dict],
        tool_calls: list[dict]
    ) -> None:
        """Remove failed tool call results from messages.

        This is aggressive — only use with EVICT strategy.
        """
        # Find and remove user messages containing error patterns
        error_patterns = ["ERROR", "error", "Failed", "failed", "Exception"]
        tc_ids = {tc.get("id", "") for tc in tool_calls}

        messages[:] = [
            msg for msg in messages
            if not (
                msg.get("role") == "user"
                and any(pat in msg.get("content", "") for pat in error_patterns)
            )
        ]

    def get_decision_context(self) -> ErrorDecisionContext:
        """Return clean decision context for orchestrator.

        Main task sees: success/failure + metrics, NOT error traces.
        This is the ONLY thing the orchestrator should use for decisions.

        If SelfEvolutionEngine is wired, includes cross-session learned recovery.
        """
        recent = self._trajectories[-3:]  # Last 3 failures
        recent_failures = len([t for t in recent if t.phase in {"ACT", "VERIFY"}])

        # Analyze last trajectory for action guidance
        last_trajectory = self._trajectories[-1] if self._trajectories else None
        last_category = last_trajectory.error_category if last_trajectory else ErrorCategory.UNKNOWN

        # Get learned recovery from SelfEvolutionEngine if available
        learned_recovery: Optional[str] = None
        if last_trajectory and self._self_evolution_engine:
            learned_recovery = self._self_evolution_engine.get_best_recovery(last_trajectory.error)

        # Determine escalation/decompose flags based on error type
        should_escalate = (
            last_category == ErrorCategory.PERMISSION_DENIED or
            (last_category == ErrorCategory.UNKNOWN and len(recent) >= 3)
        )
        should_decompose = (
            last_category == ErrorCategory.RESOURCE_EXHAUSTED or
            (last_trajectory and last_trajectory.strategy_action == "decompose")
        )

        # should_retry based on whether we have retries available
        should_retry = last_trajectory.strategy_retries > 0 if last_trajectory else True

        return ErrorDecisionContext(
            trajectory_count=len(self._trajectories),
            recent_failures=recent_failures,
            should_retry=should_retry,
            recovery_hint=learned_recovery or (last_trajectory.get_recovery_context().get("recovery_strategy", "unknown") if last_trajectory else None),
            last_error_phase=last_trajectory.phase if last_trajectory else None,
            last_error_category=last_category,
            should_escalate=should_escalate,
            should_decompose=should_decompose,
            root_causes=last_trajectory.get_root_causes() if last_trajectory else [],
            causal_trace=last_trajectory.causal_analysis.causal_trace if last_trajectory and last_trajectory.causal_analysis else "",
            causal_recommendations=[
                {"action": r.action, "target": r.target, "reason": r.reason}
                for r in last_trajectory.get_actionable_recommendations()
            ] if last_trajectory else [],
        )

    def get_recent_failures(self, n: int = 5) -> list[FailedTrajectory]:
        """Get the n most recent failures for analysis."""
        return self._trajectories[-n:]

    def get_phase_error_count(self, phase: str) -> int:
        """Get error count for a specific phase."""
        return self._phase_errors.get(phase, 0)

    def clear(self) -> None:
        """Clear all tracked trajectories."""
        self._trajectories.clear()
        self._phase_errors.clear()

    def to_summary(self) -> str:
        """Generate a LLM-readable summary of all failures.

        This is NOT the full trace — just a summary.
        Use this for skill capture, not for LLM context pollution.
        """
        if not self._trajectories:
            return "No failures recorded."

        lines = [f"Total failures: {len(self._trajectories)}"]
        for phase, count in self._phase_errors.items():
            lines.append(f"  {phase}: {count} error(s)")

        if self._trajectories:
            last = self._trajectories[-1]
            lines.append(f"Last failure: {last.phase} — {last.error[:100]}")

        return "\n".join(lines)