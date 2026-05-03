"""RalphLoop — Nexus Autonomous Coding Agent Orchestration Engine.

RalphLoop implements a closed-loop self-correction state machine:
    PLAN → ACT → VERIFY → REFLECT → (loop or COMMIT/ESCALATE/ABORT)

Exports:
    RalphState: State enumeration
    RalphLoop: Main orchestrator class
    Transition, TransitionContext, TransitionTrigger: Transition system
    ContextTier: Context budget tier enum
    Checkpoint: State checkpoint dataclass
    RalphLoopMetrics: Runtime metrics
    EscalationOption: User escalation options
    TRANSITION_TABLE: Complete transition table
    get_valid_transitions: Transition lookup function
"""

from .states import RalphState
from .orchestrator import (
    RalphLoop,
    ContextTier,
    Checkpoint,
    RalphLoopMetrics,
    EscalationOption,
)
from .transitions import (
    Transition,
    TransitionContext,
    TransitionTrigger,
    TRANSITION_TABLE,
    get_valid_transitions,
    get_abort_transition,
)

__all__ = [
    # States
    "RalphState",
    # Orchestrator
    "RalphLoop",
    "ContextTier",
    "Checkpoint",
    "RalphLoopMetrics",
    "EscalationOption",
    # Transitions
    "Transition",
    "TransitionContext",
    "TransitionTrigger",
    "TRANSITION_TABLE",
    "get_valid_transitions",
    "get_abort_transition",
]

__version__ = "0.1.0"
