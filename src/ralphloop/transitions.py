"""RalphLoop Transition Table with Guards.

Defines valid state transitions and their trigger conditions (guards).
Each transition specifies: source state, target state, trigger event,
guard condition, and optional action.

Transition Rules from SPEC.md:
    - PLAN → ACT: Valid spec produced
    - ACT → VERIFY: Implementation complete
    - VERIFY → REFLECT: Verification passed
    - VERIFY → PLAN: Verification failed (≤3 retries)
    - VERIFY → ESCALATE: 3 consecutive verify failures
    - REFLECT → PLAN: Next task in queue
    - REFLECT → COMMIT: All tasks done
    - Any → ABORT: Context budget POOR tier
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterator, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .states import RalphState


class TransitionTrigger(Enum):
    """Events that can trigger state transitions."""
    SPEC_VALID = auto()
    IMPLEMENTATION_COMPLETE = auto()
    VERIFICATION_PASSED = auto()
    VERIFICATION_FAILED = auto()
    MAX_RETRIES_EXCEEDED = auto()
    NEXT_TASK_AVAILABLE = auto()
    ALL_TASKS_COMPLETE = auto()
    CONTEXT_BUDGET_POOR = auto()
    USER_ESCALATION_RESPONSE = auto()


@dataclass(frozen=True)
class Transition:
    """Represents a valid state transition with guard condition.

    Attributes:
        from_state: Source RalphState.
        to_state: Target RalphState.
        trigger: Event that fires this transition.
        guard: Optional callable -> bool. True = allow transition.
        description: Human-readable transition description.
    """
    from_state: 'RalphState'
    to_state: 'RalphState'
    trigger: TransitionTrigger
    guard: Optional[Callable[['TransitionContext'], bool]] = None
    description: str = ""


@dataclass
class TransitionContext:
    """Context passed to transition guards for evaluation.

    Attributes:
        retry_count: Number of consecutive failures on current task.
        context_usage_percent: Current context budget usage (0-100).
        tasks_remaining: Number of tasks still in queue.
        current_error: Error message from last failure, if any.
        escalation_options_selected: User's escalation choice, if applicable.
    """
    retry_count: int = 0
    context_usage_percent: float = 0.0
    tasks_remaining: int = 0
    current_error: Optional[str] = None
    escalation_options_selected: Optional[str] = None


# Guard condition helpers
def has_retries_remaining(ctx: TransitionContext) -> bool:
    """Guard: allows transition if retry count < 3."""
    return ctx.retry_count < 3


def max_retries_exceeded(ctx: TransitionContext) -> bool:
    """Guard: allows transition only if retry count >= 3."""
    return ctx.retry_count >= 3


def context_not_poor(ctx: TransitionContext) -> bool:
    """Guard: allows transition if context budget not POOR."""
    return ctx.context_usage_percent < 70.0


def context_poor(ctx: TransitionContext) -> bool:
    """Guard: allows transition when context budget POOR (>=70%)."""
    return ctx.context_usage_percent >= 70.0


def has_more_tasks(ctx: TransitionContext) -> bool:
    """Guard: allows transition if tasks remain in queue."""
    return ctx.tasks_remaining > 0


def no_tasks_remaining(ctx: TransitionContext) -> bool:
    """Guard: allows transition if all tasks complete."""
    return ctx.tasks_remaining == 0


def escalation_resolved(ctx: TransitionContext) -> bool:
    """Guard: allows transition after user responds to escalation."""
    return ctx.escalation_options_selected is not None


# Transition Table - built lazily to avoid circular import issues
_TRANSITION_TABLE: Optional[list[Transition]] = None


def _build_transition_table() -> list[Transition]:
    """Build the transition table with proper imports."""
    from .states import RalphState

    return [
        # PLAN → ACT: Valid spec produced
        Transition(
            from_state=RalphState.PLAN,
            to_state=RalphState.ACT,
            trigger=TransitionTrigger.SPEC_VALID,
            guard=context_not_poor,
            description="Plan complete, dispatch to Act"
        ),

        # ACT → VERIFY: Implementation complete
        Transition(
            from_state=RalphState.ACT,
            to_state=RalphState.VERIFY,
            trigger=TransitionTrigger.IMPLEMENTATION_COMPLETE,
            guard=context_not_poor,
            description="Implementation done, verify"
        ),

        # VERIFY → REFLECT: Verification passed
        Transition(
            from_state=RalphState.VERIFY,
            to_state=RalphState.REFLECT,
            trigger=TransitionTrigger.VERIFICATION_PASSED,
            guard=None,
            description="Verification passed, reflect"
        ),

        # VERIFY → PLAN: Verification failed, retry available
        Transition(
            from_state=RalphState.VERIFY,
            to_state=RalphState.PLAN,
            trigger=TransitionTrigger.VERIFICATION_FAILED,
            guard=has_retries_remaining,
            description="Verify failed, retry plan"
        ),

        # VERIFY → ESCALATE: Max retries exceeded
        Transition(
            from_state=RalphState.VERIFY,
            to_state=RalphState.ESCALATE,
            trigger=TransitionTrigger.MAX_RETRIES_EXCEEDED,
            guard=max_retries_exceeded,
            description="Max retries, escalate to human"
        ),

        # REFLECT → PLAN: Next task in queue
        Transition(
            from_state=RalphState.REFLECT,
            to_state=RalphState.PLAN,
            trigger=TransitionTrigger.NEXT_TASK_AVAILABLE,
            guard=has_more_tasks,
            description="Reflect done, next task"
        ),

        # REFLECT → COMMIT: All tasks complete
        Transition(
            from_state=RalphState.REFLECT,
            to_state=RalphState.COMMIT,
            trigger=TransitionTrigger.ALL_TASKS_COMPLETE,
            guard=no_tasks_remaining,
            description="All tasks done, commit"
        ),

        # ESCALATE → PLAN: User selected rewrite/decompose
        Transition(
            from_state=RalphState.ESCALATE,
            to_state=RalphState.PLAN,
            trigger=TransitionTrigger.USER_ESCALATION_RESPONSE,
            guard=escalation_resolved,
            description="Escalation resolved, restart plan"
        ),

        # ESCALATE → COMMIT: User selected abandon (skip task)
        Transition(
            from_state=RalphState.ESCALATE,
            to_state=RalphState.COMMIT,
            trigger=TransitionTrigger.USER_ESCALATION_RESPONSE,
            guard=escalation_resolved,
            description="Escalation resolved with abandon, commit"
        ),

        # Any → ABORT: Context budget POOR
        Transition(
            from_state=RalphState.PLAN,
            to_state=RalphState.ABORT,
            trigger=TransitionTrigger.CONTEXT_BUDGET_POOR,
            guard=context_poor,
            description="Context budget POOR, abort"
        ),
        Transition(
            from_state=RalphState.ACT,
            to_state=RalphState.ABORT,
            trigger=TransitionTrigger.CONTEXT_BUDGET_POOR,
            guard=context_poor,
            description="Context budget POOR, abort"
        ),
        Transition(
            from_state=RalphState.VERIFY,
            to_state=RalphState.ABORT,
            trigger=TransitionTrigger.CONTEXT_BUDGET_POOR,
            guard=context_poor,
            description="Context budget POOR, abort"
        ),
        Transition(
            from_state=RalphState.REFLECT,
            to_state=RalphState.ABORT,
            trigger=TransitionTrigger.CONTEXT_BUDGET_POOR,
            guard=context_poor,
            description="Context budget POOR, abort"
        ),
    ]


def get_transition_table() -> list[Transition]:
    """Get or build the transition table (lazy initialization)."""
    global _TRANSITION_TABLE
    if _TRANSITION_TABLE is None:
        _TRANSITION_TABLE = _build_transition_table()
    return _TRANSITION_TABLE


# For backwards compatibility, expose TRANSITION_TABLE as property
class _TransitionTableAccessor:
    """Lazy-loaded transition table accessor."""

    def __iter__(self) -> Iterator[RalphState]:
        # Extract unique target states from transitions
        seen: set[RalphState] = set()
        for t in get_transition_table():
            if t.to_state not in seen:
                seen.add(t.to_state)
                yield t.to_state

    def __len__(self) -> int:
        return len(get_transition_table())


TRANSITION_TABLE = _TransitionTableAccessor()


def get_valid_transitions(
    current_state: 'RalphState',
    trigger: TransitionTrigger,
    context: TransitionContext
) -> list[Transition]:
    """Return all valid transitions matching current state and trigger.

    Args:
        current_state: Current RalphState.
        trigger: TransitionTrigger that occurred.
        context: TransitionContext for guard evaluation.

    Returns:
        List of matching Transition objects (normally 0 or 1).
    """
    valid = []
    for t in get_transition_table():
        if t.from_state == current_state and t.trigger == trigger:
            if t.guard is None or t.guard(context):
                valid.append(t)
    return valid


def get_abort_transition(current_state: 'RalphState') -> Optional[Transition]:
    """Get the ABORT transition for a given state, if one exists."""
    from .states import RalphState

    for t in get_transition_table():
        if t.from_state == current_state and t.to_state == RalphState.ABORT:
            return t
    return None
