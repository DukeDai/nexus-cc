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
    ImplementationContext: Shared state container
    TDDEnforcer: Test-Driven Development enforcement
    TDDCycle, TDDCycleResult, TDDPhase: TDD cycle types
    TRANSITION_TABLE: Complete transition table
    get_valid_transitions: Transition lookup function
"""

from .states import RalphState  # State enumeration for RalphLoop state machine

from .orchestrator import (
    RalphLoop,  # Main orchestrator class
    Checkpoint,  # State checkpoint dataclass
    ContextTier,  # Context budget tier enum
    EscalationOption,  # User escalation options
    RalphLoopMetrics,  # Runtime metrics
)

from .implementation_context import (
    ImplementationContext,  # Shared mutable state container
)

from .tdd_enforcer import (
    TDDEnforcer,  # Test-Driven Development enforcement
    TDDCycle,  # TDD cycle tracking
    TDDCycleResult,  # TDD cycle result
    TDDPhase,  # TDD phase enum
)

from .transitions import (
    Transition,  # State transition definition
    TransitionContext,  # Transition context
    TransitionTrigger,  # Transition trigger enum
    TRANSITION_TABLE,  # Complete transition table
    get_valid_transitions,  # Transition lookup function
    get_abort_transition,  # Abort transition lookup
)

from .agent_loop import (
    run_agent_loop,  # Real LLM-driven closed loop
    TOOL_DEFINITIONS,  # Tool definitions for LLM
    ToolExecutor,  # Tool execution engine
    LoopResult,  # Loop result type
    AgentLoopConfig,  # Loop configuration
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
    # Implementation context
    "ImplementationContext",
    # TDD enforcement
    "TDDEnforcer",
    "TDDCycle",
    "TDDCycleResult",
    "TDDPhase",
    # Transitions
    "Transition",
    "TransitionContext",
    "TransitionTrigger",
    "TRANSITION_TABLE",
    "get_valid_transitions",
    "get_abort_transition",
    # Agent loop
    "run_agent_loop",
    "TOOL_DEFINITIONS",
    "ToolExecutor",
    "LoopResult",
    "AgentLoopConfig",
]

__version__ = "0.1.0"
