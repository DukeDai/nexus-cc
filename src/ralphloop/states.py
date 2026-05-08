"""RalphLoop State Definitions.

Defines the canonical states for the RalphLoop state machine:
PLAN -> ACT -> VERIFY -> REFLECT -> (loop or COMMIT/ESCALATE/ABORT)
"""

from enum import Enum, auto


class RalphState(Enum):
    """RalphLoop state enumeration.

    States represent distinct phases in the autonomous coding loop.
    Each state has explicit entry/exit semantics enforced by guards.
    """

    PLAN = auto()
    """Planning phase: understand requirements, write spec."""

    ACT = auto()
    """Action phase: implement code and tests per spec."""

    VERIFY = auto()
    """Verification phase: run gates (TDD, security, review)."""

    REFLECT = auto()
    """Reflection phase: analyze outcomes, capture learnings."""

    COMMIT = auto()
    """Final state: all tasks complete, ready to commit."""

    ESCALATE = auto()
    """Escalation state: requires human decision after retries exhausted."""

    ABORT = auto()
    """Abort state: context budget POOR tier, checkpoint and stop."""

    # Aliases for common composite transitions
    class TransitionTarget(Enum):
        """Composite transition targets for convenience."""
        NEXT_TASK = auto()
        RETRY_TASK = auto()
        FINISH = auto()
