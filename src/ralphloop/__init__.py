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

from .subagent_registry import (
    SubagentDefinition,  # Subagent role definition
    SUBAGENT_DEFINITIONS,  # All subagent definitions
    get_subagent,  # Get subagent by name
    get_all_subagents,  # Get all subagents
)

from .claude_md_loader import (
    ProjectContext,  # Project context manager
    load_claude_md,  # Load CLAUDE.md
    find_project_root,  # Find project root
    find_claude_md,  # Find CLAUDE.md path
    build_llm_system_prompt,  # Build LLM system prompt
    get_project_context,  # Get full project context
)

from .subagent_integration import (
    SubagentIntegration,  # Subagent orchestration bridge
    SubagentResult,  # Subagent execution result
    OrchestratedResult,  # Aggregated orchestration result
    orchestrate_with_subagents,  # High-level entry point
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
    # Subagent system
    "SubagentDefinition",
    "SUBAGENT_DEFINITIONS",
    "get_subagent",
    "get_all_subagents",
    # CLAUDE.md loader
    "ProjectContext",
    "load_claude_md",
    "find_project_root",
    "find_claude_md",
    "build_llm_system_prompt",
    "get_project_context",
    # Subagent integration
    "SubagentIntegration",
    "SubagentResult",
    "OrchestratedResult",
    "orchestrate_with_subagents",
]

__version__ = "0.1.0"
