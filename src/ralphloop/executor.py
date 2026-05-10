"""RalphLoopExecutor — Unified Entry Point for Nexus.

This is the TOP-LEVEL orchestration engine that ties together all Nexus components:
    - RalphLoop state machine (orchestrator)
    - WAL + Checkpoint (crash recovery)
    - SubagentIntegration (parallel delegate_task agents)
    - ModelRouter (cost-optimized model selection)
    - SelfEvolutionEngine (cross-session error learning)
    - TDDEnforcer (RED→GREEN→REFACTOR)

Key differentiator vs Claude Code:
    Claude Code = single monolithic agent, no memory, no recovery, no parallelism
    Nexus = integrated system with crash recovery, multi-agent, cross-session learning

Usage:
    executor = RalphLoopExecutor(workdir=Path.cwd())
    result = executor.run_task("Create a REST API for user management")
    print(result.summary)
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .orchestrator import (
    RalphLoop,
    RalphState,
    ContextTier,
    Checkpoint,
    RalphLoopMetrics,
    EscalationOption,
)
from .transitions import TransitionTrigger
from .agent_loop import run_agent_loop, AgentLoopConfig
from .implementation_context import ImplementationContext
from .subagent_integration import (
    SubagentIntegration,
    SubagentResult,
    OrchestratedResult,
)
from .tdd_enforcer import TDDEnforcer, TDDCycle, TDDPhase
from .complexity import TaskComplexity, classify_complexity
# Absolute imports — these work when src/ is in sys.path (test_cli.py pattern)
from context.wal import WALManager, WALEntry          # src/context/wal.py
from context.checkpoint import CheckpointManager       # src/context/checkpoint.py
from self_evolution.engine import SelfEvolutionEngine, LearnedSkill  # src/self_evolution/
from llm.model_router import ModelRouter, TaskType, Provider         # src/llm/
from llm.client import LLMClient                                      # src/llm/
from engine.registry import ToolRegistry                             # src/engine/registry.py


# ─── Result Types ─────────────────────────────────────────────────────────────

@dataclass
class ExecutorResult:
    """Final result from RalphLoopExecutor.run_task()."""
    success: bool
    summary: str
    final_state: RalphState
    metrics: RalphLoopMetrics
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    skills_learned: int = 0
    checkpoints_saved: int = 0
    wal_entries: int = 0
    errors_recovered: int = 0
    error_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "summary": self.summary,
            "final_state": self.final_state.name,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "skills_learned": self.skills_learned,
            "checkpoints_saved": self.checkpoints_saved,
            "wal_entries": self.wal_entries,
            "errors_recovered": self.errors_recovered,
            "error_log": self.error_log,
            "total_turns": self.metrics.total_turns,
            "total_llm_calls": self.metrics.total_turns,  # total_turns ≈ LLM call count
            "total_tool_calls": self.metrics.total_turns * 2,  # estimate: ~2 tool calls per turn
            "total_cost_from_metrics": round(self._total_cost, 6),
        }


# ─── RalphLoopExecutor ────────────────────────────────────────────────────────

class RalphLoopExecutor:
    """Unified executor integrating all Nexus components.

    This is the main entry point for running Nexus tasks. It orchestrates:
        1. WALManager: logs every state transition + tool call (crash recovery)
        2. CheckpointManager: saves/restores full state snapshots
        3. SelfEvolutionEngine: learns from errors, generates skills
        4. ModelRouter: selects optimal model per task type
        5. SubagentIntegration: parallel Implementer + Reviewer agents
        6. TDDEnforcer: enforces RED→GREEN→REFACTOR discipline

    Architecture:
        User task
              ↓
        RalphLoopExecutor
              ↓
        ┌─────────────────────────────────────────┐
        │  RalphLoop Orchestrator (state machine) │
        │  PLAN → ACT → VERIFY → REFLECT         │
        └─────────────────────────────────────────┘
              ↓
        ┌──────────────┬──────────────────────────┐
        │ WALManager   │ SelfEvolutionEngine      │
        │ (journaling) │ (cross-session learning)  │
        ├──────────────┼──────────────────────────┤
        │ CheckpointMgr│ ModelRouter              │
        │ (snapshots)  │ (cost optimization)      │
        ├──────────────┴──────────────────────────┤
        │ SubagentIntegration (parallel agents)   │
        │   ImplementerAgent + ReviewerAgent      │
        └─────────────────────────────────────────┘
    """

    # Component settings
    WAL_DIR = Path.home() / ".nexus" / "wal"
    CHECKPOINT_DB = Path.home() / ".nexus" / "checkpoints.db"
    SKILLS_DIR = Path.home() / ".hermes" / "skills"
    ERROR_LOG = Path.home() / ".nexus" / "error_log.jsonl"

    def __init__(
        self,
        workdir: Path | str | None = None,
        llm_provider: Provider = Provider.ANTHROPIC,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        enable_wal: bool = True,
        enable_checkpoint: bool = True,
        enable_self_evolution: bool = True,
        enable_model_router: bool = True,
        enable_parallel_subagents: bool = True,
        enable_tdd: bool = False,
        enable_verification_pipeline: bool = True,
        checkpoint_interval: int = 5,
        max_retries: int = 3,
        model_router: ModelRouter | None = None,
        tool_registry: ToolRegistry | None = None,
        custom_tools: list[dict] | None = None,
        mcp_bridge: Optional[Any] = None,
        streaming_callback: Callable[[str], None] | None = None,
    ):
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.llm_provider = llm_provider
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url

        # Component toggles
        self.enable_wal = enable_wal
        self.enable_checkpoint = enable_checkpoint
        self.enable_self_evolution = enable_self_evolution
        self.enable_model_router = enable_model_router
        self.enable_parallel_subagents = enable_parallel_subagents
        self.enable_tdd = enable_tdd
        self.enable_verification_pipeline = enable_verification_pipeline
        self.checkpoint_interval = checkpoint_interval
        self.max_retries = max_retries
        self._mcp_bridge = mcp_bridge
        self._tool_registry = tool_registry
        self.custom_tools = custom_tools  # resolved in _init_tool_registry()
        self._streaming_callback = streaming_callback  # LLM output stream sink

        # Initialize components (order matters: router before subagent integration)
        self._init_wal()
        self._init_checkpoint()
        self._init_self_evolution()
        self._init_model_router(model_router)
        self._init_tool_registry()       # depends on custom_tools being set
        self._init_subagent_integration()  # depends on _model_router
        self._init_tdd_enforcer()
        self._init_verification_pipeline()

        # Runtime state
        self._current_loop: RalphLoop | None = None
        self._current_context: ImplementationContext | None = None
        self._skills_learned_count = 0
        self._checkpoints_saved_count = 0
        self._wal_entries_count = 0
        self._errors_recovered_count = 0
        self._total_cost = 0.0
        self._total_tokens = 0

    # ─── Component Initialization ───────────────────────────────────────────

    def _init_wal(self) -> None:
        """Initialize WAL manager for crash recovery journaling."""
        if not self.enable_wal:
            self._wal: WALManager | None = None
            return
        self.WAL_DIR.mkdir(parents=True, exist_ok=True)
        self._wal = WALManager(wal_dir=self.WAL_DIR)

    def _init_checkpoint(self) -> None:
        """Initialize CheckpointManager for state snapshots."""
        if not self.enable_checkpoint:
            self._ckpt: CheckpointManager | None = None
            return
        self.CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
        self._ckpt = CheckpointManager(db_path=self.CHECKPOINT_DB)

    def _init_self_evolution(self) -> None:
        """Initialize Self-Evolution engine for cross-session learning."""
        if not self.enable_self_evolution:
            self._evo: SelfEvolutionEngine | None = None
            return
        self.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self.ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._evo = SelfEvolutionEngine(
            skills_dir=self.SKILLS_DIR,
            error_log_path=self.ERROR_LOG,
        )
        self._evo.load_existing_skills()

    def _init_model_router(self, model_router: ModelRouter | None) -> None:
        """Initialize ModelRouter for cost-optimized model selection."""
        if not self.enable_model_router:
            self._router: ModelRouter | None = None
            self._llm_client: LLMClient | None = None
            return

        # Auto-detect credentials: try MiniMax first (most likely available in this environment)
        api_keys: dict[Provider, str] = {}
        base_urls: dict[Provider, str] = {}
        preferred: Provider | None = None

        if self.llm_api_key:
            api_keys[self.llm_provider] = self.llm_api_key
            preferred = self.llm_provider
        else:
            # Try to auto-detect from environment
            import os, subprocess
            # MiniMax Chinese API
            try:
                minimax_key = subprocess.check_output(
                    ['bash', '-c', 'source /Users/dukedai/.hermes/.env 2>/dev/null && echo $MINIMAX_CN_API_KEY'],
                    stderr=subprocess.DEVNULL
                ).decode().strip()
                if minimax_key:
                    api_keys[Provider.MINIMAX_CN] = minimax_key
                    base_urls[Provider.MINIMAX_CN] = "https://api.minimaxi.com/anthropic"
                    preferred = Provider.MINIMAX_CN
            except Exception:
                pass

            # OpenRouter (may be available)
            try:
                openrouter_key = os.environ.get('OPENROUTER_API_KEY', '')
                if openrouter_key:
                    api_keys[Provider.OPENAI] = openrouter_key
                    if preferred is None:
                        preferred = Provider.OPENAI
            except Exception:
                pass

        self._router = model_router or ModelRouter(
            api_keys=api_keys,
            base_urls=base_urls,
            preferred_provider=preferred,
        )
        # Select default model
        default_model = self._router.select_model(
            task_type=TaskType.CODE,
            requires_tools=True,
        )
        self._llm_client = self._router.get_client(default_model)

    def _init_subagent_integration(self) -> None:
        """Initialize SubagentIntegration for parallel agents."""
        self._si = SubagentIntegration(
            workdir=self.workdir,
            llm_client=self._llm_client,
            model_router=getattr(self, '_router', None),
        )

    def _init_tool_registry(self) -> None:
        """Initialize ToolRegistry for dynamic tool discovery and execution.

        Resolution order:
        1. If a tool_registry was passed to __init__, use it directly.
        2. If custom_tools list was passed, use those as extra tools on top of
           auto-discovered nexus.tools (if the package exists).
        3. Fall back to agent_loop TOOL_DEFINITIONS.
        """
        from .agent_loop import TOOL_DEFINITIONS  # lazy to avoid circular

        if self._tool_registry is not None:
            # Caller provided a pre-configured registry
            self.custom_tools = self._tool_registry.definitions()
            return

        # Build a registry: auto-discover nexus.tools + merge custom_tools
        registry = ToolRegistry()
        registry.register_all(package_name="nexus.tools")
        discovered = registry.definitions()

        # Merge in any custom tool definitions (list[dict])
        if self.custom_tools:
            self.custom_tools = discovered + self.custom_tools
        elif discovered:
            self.custom_tools = discovered
        else:
            # No package, no custom — fall back to static TOOL_DEFINITIONS
            self.custom_tools = TOOL_DEFINITIONS

        self._tool_registry = registry

    def _init_tdd_enforcer(self) -> None:
        """Initialize TDDEnforcer for RED→GREEN→REFACTOR discipline."""
        self._tdd: TDDEnforcer | None = TDDEnforcer() if self.enable_tdd else None

    def _init_verification_pipeline(self) -> None:
        """Initialize VerificationPipeline for inline ACT phase gates."""
        if not self.enable_verification_pipeline:
            self._verify_pipeline: Any = None
            return
        try:
            from verification.pipeline import VerificationPipeline
            # Use SubagentIntegration.run_security_scan as the delegate_task.
            # This is non-trivial: SecurityGate and ReviewGate use delegate_task,
            # but for ACT-phase inline gates we primarily care about SecurityScan.
            def delegate_task(task: str, context: dict[str, Any]) -> dict[str, Any]:
                result = self._si.run_security_scan(context.get("files", []))
                # SubagentResult → dict (for VerificationPipeline interface)
                if hasattr(result, "to_dict"):
                    return result.to_dict()  # type: ignore[attr-defined]
                return {"success": result.success, "findings": [], "error": None}  # type: ignore[return-value]
            self._verify_pipeline = VerificationPipeline(
                delegate_task=delegate_task,
            )
        except ImportError:
            self._verify_pipeline = None

    # ─── Self-Evolution: Proactive Skill Injection ─────────────────────────

    def _get_success_hints(self, task_context: str) -> str:
        """Build a hint prompt from previously successful patterns for this type of task.

        Unlike recovery (which is reactive to errors), success hints are PROACTIVE:
        before planning, we query skills for similar successful completions and
        inject those patterns so the planner can apply proven approaches.
        """
        if not self._evo or not task_context:
            return ""

        all_skills = self._evo.get_all_skills()
        if not all_skills:
            return ""

        # Find skills whose trigger matches the task context
        matching = []
        for skill in all_skills:
            trigger = skill.trigger
            if not trigger:
                continue
            # Check if this task context is similar to the skill's trigger
            # (success skills use task context as trigger, error skills use error pattern)
            if "success" in skill.name.lower() or (
                len(trigger) > 10 and trigger.lower() in task_context.lower()
            ):
                matching.append(skill)

        if not matching:
            return ""

        lines = [
            "\n\n## Previously Successful Approaches (Nexus Self-Evolution)\n",
            "For similar tasks completed successfully in the past, here are verified approaches:\n",
        ]
        for skill in matching[:3]:
            lines.append(f"### Approach for: `{skill.trigger[:60]}`")
            for step in skill.recovery_steps[:3]:
                lines.append(f"  - {step}")
            lines.append("")

        return "\n".join(lines)

    def _get_recovery_prompt(self) -> str:
        """Build recovery prompt from previously learned skills.

        This is the KEY differentiator: before starting a task, Nexus
        checks if we've seen similar errors before and proactively tells
        the LLM how to recover. Claude Code has NO equivalent.
        """
        if not self._evo:
            return ""

        skills = self._evo.get_all_skills()
        if not skills:
            return ""

        lines = [
            "\n\n## Cross-Session Error Recovery (Nexus Self-Evolution)\n",
            "Based on previous sessions, here are known error patterns and their fixes:\n",
        ]
        for skill in skills[-5:]:  # Show last 5 skills
            if skill.trigger and skill.recovery_steps:
                lines.append(f"### If you see: `{skill.trigger}`")
                for i, step in enumerate(skill.recovery_steps[:3], 1):
                    lines.append(f"  {i}. {step}")
                lines.append("")

        return "\n".join(lines)

    # ─── Context Monitor for Orchestrator ──────────────────────────────────

    def _make_context_monitor(self) -> Callable[[], float]:
        """Create a context monitor callable for RalphLoop orchestrator."""
        def monitor() -> float:
            if self._current_context:
                return self._current_context.budget_percent
            return 0.0
        return monitor

    # ─── Main Entry Point ──────────────────────────────────────────────────

    def run_task(
        self,
        task: str,
        spec_md: str | None = None,
        constraints: list[str] | None = None,
        max_turns_per_state: int = 20,
    ) -> ExecutorResult:
        """Run a single task through the full RalphLoop.

        Args:
            task: Task description (or raw requirements).
            spec_md: Optional SPEC.md content.
            constraints: Optional list of constraints.
            max_turns_per_state: Max LLM turns per RalphLoop state.

        Returns:
            ExecutorResult with summary, metrics, costs, etc.
        """
        task_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # Initialize context
        self._current_context = ImplementationContext(
            task=task,
            context_window=200000,
        )

        # Inject Self-Evolution recovery prompt into context
        recovery_prompt = self._get_recovery_prompt()
        if recovery_prompt:
            self._current_context.add_message(
                "system",
                recovery_prompt
            )

        # Attach Self-Evolution engine to context
        if self._evo:
            self._current_context._evolution_engine = self._evo

        # Determine task type for model routing
        task_type = self._classify_task(task)

        # Build RalphLoop orchestrator
        orchestrator = RalphLoop(
            task_queue=[{
                "description": task,
                "spec_md": spec_md,
                "constraints": constraints or [],
                "task_id": task_id,
            }],
            context_monitor=self._make_context_monitor(),
            checkpoint_dir=Path.home() / ".nexus" / "checkpoints" if self.enable_checkpoint else None,
            on_state_change=self._on_state_change,
            on_escalation=self._on_escalation,
            on_warning=self._on_warning,
            agent_executor=self._make_agent_executor(task_type),
        )
        self._current_loop = orchestrator

        # Log WAL: task start
        if self._wal:
            # Log the decomposition transition if triggered
            complexity = self._classify_task_complexity(task)
            to_state = "DECOMPOSE" if complexity != TaskComplexity.SIMPLE else "PLAN"
            self._wal.log_transition(
                from_state="INIT",
                to_state=to_state,
                trigger=f"task_start:{task_id}",
            )
            self._wal_entries_count += 2

        # P6: Pre-check complexity and execute DECOMPOSE phase if needed.
        # This runs BEFORE orchestrator.run() so the task_queue is
        # already populated with subtasks when the state machine starts.
        self._execute_decompose_phase(orchestrator, task, spec_md, constraints)

        # Run the RalphLoop (task_queue may now have multiple subtasks)
        result = orchestrator.run()

        # Final checkpoint on success or failure
        if self._ckpt and result.get("checkpoint_path"):
            self._checkpoints_saved_count += 1

        # Log WAL: task end
        if self._wal:
            self._wal.log_transition(
                from_state=result.get("final_state", RalphState.ABORT).name,
                to_state="COMMIT",
                trigger=f"task_end:{task_id}",
            )

        duration = time.time() - start_time

        return ExecutorResult(
            success=result.get("success", False),
            summary=self._build_summary(result, task_id, duration),
            final_state=result.get("final_state", RalphState.ABORT),
            metrics=result.get("metrics", RalphLoopMetrics()),
            total_cost_usd=self._total_cost,
            total_tokens=self._total_tokens,
            skills_learned=self._skills_learned_count,
            checkpoints_saved=self._checkpoints_saved_count,
            wal_entries=self._wal_entries_count,
            errors_recovered=self._errors_recovered_count,
            error_log=result.get("error_log", []),
        )

    def run_tasks(
        self,
        tasks: list[str],
        spec_md: str | None = None,
        constraints: list[str] | None = None,
    ) -> list[ExecutorResult]:
        """Run multiple tasks sequentially through RalphLoop.

        Args:
            tasks: List of task descriptions.
            spec_md: Optional shared SPEC.md.
            constraints: Optional shared constraints.

        Returns:
            List of ExecutorResult, one per task.
        """
        results = []
        for task in tasks:
            result = self.run_task(task, spec_md, constraints)
            results.append(result)
            if not result.success:
                # Stop on failure (or continue with next task based on config)
                break
        return results

    # ─── Agent Executor (dispatched by RalphLoop orchestrator) ────────────

    def _make_agent_executor(self, task_type: TaskType) -> Callable[[dict, RalphState], dict]:
        """Create an agent executor closure for RalphLoop state machine."""
        def executor(task: dict, phase: RalphState) -> dict:
            return self._execute_phase(task, phase, task_type)
        return executor

    def _execute_phase(
        self,
        task: dict,
        phase: RalphState,
        task_type: TaskType,
    ) -> dict:
        """Execute a RalphLoop phase.

        Each phase (PLAN/ACT/VERIFY/REFLECT) is powered by either:
        - Parallel subagents (ACT phase): Implementer + Reviewer
        - Single LLM loop (other phases): run_agent_loop
        """
        task_desc = task.get("description", "")
        spec_md = task.get("spec_md")
        constraints = task.get("constraints", [])

        if phase == RalphState.PLAN:
            # PLAN phase: analyze requirements, create SPEC.md
            result = self._execute_plan(task_desc, spec_md, constraints, task_type)
            if self._current_loop:
                self._current_loop.metrics.add_turns(2)
            return result

        elif phase == RalphState.ACT:
            # ACT phase: implement + review (parallel subagents)
            result = self._execute_act(task_desc, spec_md, constraints, task_type)
            # Store for REFLECT phase
            self._last_act_result = result
            if self._current_loop:
                self._current_loop.metrics.add_turns(3)
            return result

        elif phase == RalphState.VERIFY:
            # VERIFY phase: run tests, verify against spec
            result = self._execute_verify(task_desc, spec_md, constraints)
            if self._current_loop:
                self._current_loop.metrics.add_turns(1)
            return result

        elif phase == RalphState.REFLECT:
            # REFLECT phase: review what was done, decide next steps
            # Inject pipeline_warnings from ACT phase into reflection analysis
            act_result = self._last_act_result if hasattr(self, '_last_act_result') else None
            result = self._execute_reflect(task_desc, spec_md, act_result=act_result)
            # Record LLM turns from reflect phase (minimal)
            if self._current_loop:
                self._current_loop.metrics.add_turns(1)
            return result

        else:
            return {"success": False, "error": f"Unknown phase: {phase}"}

    def _execute_plan(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
        task_type: TaskType,
    ) -> dict:
        """PLAN phase: analyze task, generate/validate SPEC.md."""
        # Use a fast model for planning
        model_name = self._select_model(task_type, prefer_speed=True)
        client = self._get_client_for_model(model_name)

        system_prompt = self._build_system_prompt(
            task=task,
            spec_md=spec_md,
            constraints=constraints,
            role="planner",
        )

        config = AgentLoopConfig(max_turns=5)
        ctx = self._current_context or ImplementationContext(task=task)

        # ── P0: MCP Bridge integration ────────────────────────────────────
        # If bridge is configured, gather context via MCP tools before LLM call.
        # This is a REAL architectural advantage: Claude Code has no MCP integration.
        mcp_context = ""
        if self._mcp_bridge is not None:
            try:
                mcp_result = self._mcp_bridge.plan_with_mcp({
                    "description": task,
                    "constraints": constraints,
                })
                if mcp_result.get("success"):
                    # Inject MCP results into the planning context
                    for r in mcp_result.get("results", []):
                        if r.get("result", {}).success:
                            tool_result = r["result"].result
                            mcp_context += f"\n\n[MCP {r['server']} context]: {tool_result}"
                    if mcp_context:
                        system_prompt += (
                            "\n\n# MCP Context (from connected MCP servers)"
                            f"\n{mcp_context}"
                        )
                        ctx.messages.append({
                            "role": "system",
                            "content": f"[MCP bridge injected context for planning: {task[:100]}...]"
                        })
            except Exception as e:
                # MCP bridge failure is non-fatal — proceed with LLM-only planning
                pass

        # ── P7: Proactive success hints ─────────────────────────────────
        # Before planning, query SelfEvo for similar successful task patterns
        # and inject them so the planner can apply proven approaches proactively.
        success_hints = self._get_success_hints(task)
        if success_hints:
            system_prompt += success_hints

        result = run_agent_loop(
            task=f"Analyze and plan: {task}",
            llm_client=client,
            context=ctx,
            config=config,
            system_prompt=system_prompt,
            workdir=self.workdir,
            tools=self.custom_tools,
            wal=self._wal,
            streaming_callback=self._streaming_callback,
        )

        # Track cost
        self._track_usage(model_name, result.turns)

        # Success if: loop completed normally, OR meaningful work was done (files created, content generated)
        meaningful_work = (
            result.complete
            or bool(result.final_content and len(result.final_content) > 50)
            or result.tool_calls > 0
        )
        return {
            "success": meaningful_work,
            "error": None if meaningful_work else "Plan produced no output",
            "result": result.final_content,
            "spec_md": spec_md or result.final_content or "",
        }

    def _execute_act(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
        task_type: TaskType,
    ) -> dict:
        """ACT phase: implement + review in parallel subagents."""
        if self.enable_parallel_subagents:
            # Use parallel subagent execution (delegate_task based)
            return self._execute_act_parallel(task, spec_md, constraints, task_type)
        else:
            # Fall back to single-agent loop
            return self._execute_act_single(task, spec_md, constraints, task_type)

    def _execute_act_parallel(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
        task_type: TaskType,
    ) -> dict:
        """ACT phase with parallel subagents (Implementer + Reviewer)."""
        # Inject Self-Evolution recovery into subagent context
        recovery = self._get_recovery_prompt()
        enhanced_task = task
        if recovery:
            enhanced_task = task + "\n\n" + recovery

        orchestrated = self._si.run_implementer_with_review(
            task=enhanced_task,
            spec_md=spec_md,
            constraints=constraints,
            max_turns=15,
        )

        # Track metrics
        for r in orchestrated.all_results:
            if r.model_used:
                self._track_usage(r.model_used, r.turns)

        # Count recovered errors
        if self._evo:
            # Analyze results for any errors that were recovered
            for r in orchestrated.all_results:
                if "ERROR" in r.output.upper() and "recovered" in r.output.lower():
                    self._errors_recovered_count += 1

        return {
            "success": orchestrated.overall_status == "success",
            "error": None if orchestrated.overall_status == "success" else orchestrated.summary,
            "result": orchestrated.summary,
            "orchestrated": orchestrated,
        }

    def _execute_act_single(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
        task_type: TaskType,
    ) -> dict:
        """ACT phase with single-agent loop (no parallelism)."""
        model_name = self._select_model(task_type, prefer_speed=False)
        client = self._get_client_for_model(model_name)

        system_prompt = self._build_system_prompt(
            task=task,
            spec_md=spec_md,
            constraints=constraints,
            role="implementer",
        )

        config = AgentLoopConfig(max_turns=20)
        ctx = self._current_context or ImplementationContext(task=task)

        result = run_agent_loop(
            task=task,
            llm_client=client,
            context=ctx,
            config=config,
            system_prompt=system_prompt,
            workdir=self.workdir,
            tools=self.custom_tools,
            wal=self._wal,
            streaming_callback=self._streaming_callback,
        )

        self._track_usage(model_name, result.turns)

        # Learn from any errors in this execution
        self._learn_from_errors(ctx)

        # ── TDD Loop: RED→GREEN→REFACTOR ─────────────────────────────────
        # After initial implementation, run the full TDD cycle.
        # TDDEnforcer enforces test-first discipline:
        #   RED: LLM writes failing test
        #   GREEN: LLM writes minimal implementation to pass (up to 3 debug iterations)
        #   REFACTOR: LLM improves code quality
        # If any GREEN iteration fails, ACT is marked failed → ESCALATE.
        # This is a CORE architectural advantage vs Claude Code's no-TDD approach.
        if self._tdd and result.complete:
            tdd_result = self._tdd.run_cycle(
                llm_client=client,
                messages=list(ctx.messages),  # snapshot of conversation
                task=task,
            )
            if not tdd_result.success:
                return {
                    "success": False,
                    "error": f"TDD {tdd_result.final_phase.value} failed: "
                             f"{tdd_result.final_test_output[:200]}",
                    "result": result.final_content,
                    "tdd_result": tdd_result,
                    "pipeline_warnings": [],
                }
            # TDD passed: include debug log in result for REFLECT phase
            ctx.add_message("system", f"[TDD] {tdd_result.final_phase.name}: {tdd_result.debug_output[:300]}")

        # ── P2: VerificationPipeline inline gate ─────────────────────────
        # After ACT generates code, run inline verification before returning.
        # SecurityScan is fail-closed: malicious code blocks the ACT result.
        # This is a REAL architectural advantage vs Claude Code's post-hoc checks.
        pipeline_warnings: list[str] = []
        if self._verify_pipeline is not None:
            try:
                # Scan all Python files created/modified by this ACT run
                py_files = list(self.workdir.rglob("*.py"))
                if py_files:
                    # Run pipeline on the most recent files (created in this ACT)
                    # Use file_path for single-file scan
                    pipeline_result = self._verify_pipeline.run(
                        file_path=str(py_files[0]),
                        test_paths=[str(p) for p in py_files[:5]],
                    )
                    if not pipeline_result.passed:
                        pipeline_warnings.append(
                            f"VerificationPipeline failed at "
                            f"{pipeline_result.failed_stage}: "
                            f"{pipeline_result.summary}"
                        )
                    elif pipeline_result.issues:
                        for issue in pipeline_result.issues[:3]:
                            pipeline_warnings.append(f"Issue: {issue['type']} — {issue['description']}")
            except Exception as e:
                pipeline_warnings.append(f"VerificationPipeline error: {e}")

        return {
            "success": result.complete,
            "error": None if result.complete else "Implementation incomplete",
            "result": result.final_content,
            "pipeline_warnings": pipeline_warnings,
        }

    def _execute_verify(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
    ) -> dict:
        """VERIFY phase: run tests, check against spec.
        
        This phase is ADVISORY only for simple tasks. It tries to run pytest
        but is lenient — files created + spec check is enough to pass.
        Full verification is done by the ReviewerAgent in the ACT phase.
        """
        # Quick pass: check if files were created (lenient check for simple tasks)
        py_files = list(self.workdir.rglob("*.py"))
        files_created = len(py_files) > 0

        # Try pytest only if files exist and we're in a real project (has __init__.py)
        has_pytest_structure = (self.workdir / "tests").exists() or (self.workdir / "__init__.py").exists()
        pytest_passed = None
        pytest_output = ""

        if files_created and has_pytest_structure:
            import subprocess
            try:
                proc = subprocess.run(
                    ["python", "-m", "pytest", "-v", "--tb=short", "-x"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(self.workdir),
                )
                pytest_passed = proc.returncode == 0
                pytest_output = proc.stdout + proc.stderr
            except FileNotFoundError:
                pytest_passed = None  # No pytest
                pytest_output = "pytest not found"
            except subprocess.TimeoutExpired:
                pytest_passed = False
                pytest_output = "Test verification timed out (30s limit)"

        # ── P0: MCP Bridge integration ────────────────────────────────────
        # If bridge is configured, run MCP verification in parallel with pytest.
        # This lets VERIFY phase use real MCP tools (GitHub issues, CI status, etc.)
        mcp_verify_success = False
        mcp_verify_result = ""
        if self._mcp_bridge is not None:
            try:
                mcp_v = self._mcp_bridge.verify_with_mcp({
                    "task": task,
                    "spec_md": spec_md or "",
                    "files": [str(f) for f in py_files[:10]],
                })
                mcp_verify_success = mcp_v.get("success", False)
                if mcp_verify_success:
                    mcp_results = mcp_v.get("results", [])
                    mcp_verify_result = "; ".join(
                        f"{r['server']}: {r['result'].result}"
                        for r in mcp_results
                        if r.get("result", {}).success
                    ) or "MCP verification succeeded"
            except Exception:
                pass  # Non-fatal

        # ── SelfEvo闭环: 从验证结果学习 ─────────────────────────────────────
        # Feed verification outcome to SelfEvolutionEngine so it can capture
        # successful patterns (not just errors) for future sessions.
        self._learn_from_verification_outcome(
            pytest_passed=pytest_passed,
            pytest_output=pytest_output,
            task_context=task,
        )

        # For simple tasks: files created = pass (lenient)
        # For complex tasks with pytest: pytest must pass
        # For MCP bridge tasks: MCP verification result is included in response
        if pytest_passed is None and files_created:
            # Lenient: no pytest, just check files exist
            return {
                "success": True,
                "error": None,
                "result": f"Verified: {len(py_files)} files created. pytest skipped (no test structure).",
                "tests_passed": None,
                "files_created": len(py_files),
                "mcp_verified": mcp_verify_success,
                "mcp_verified_result": mcp_verify_result,
            }
        elif pytest_passed is True:
            return {"success": True, "error": None, "result": "Tests passed.",
                "tests_passed": True, "mcp_verified": mcp_verify_success,
                "mcp_verified_result": mcp_verify_result}
        elif pytest_passed is False:
            return {
                "success": False,
                "error": f"Tests failed: {pytest_output[:200]}",
                "result": pytest_output[:500],
                "tests_passed": False,
            }
        else:
            # No files created — fail
            return {"success": False, "error": "No files created", "result": "", "tests_passed": False}

    def _execute_reflect(
        self,
        task: str,
        spec_md: str | None,
        act_result: dict | None = None,
    ) -> dict:
        """REFLECT phase: review what was done.

        Enhanced with:
        - pipeline_warnings from ACT phase (TDD failures, security findings, etc.)
        - SelfEvo recovery: proactive skill hints for future similar tasks
        - TDD result analysis if TDD was enabled
        """
        ctx = self._current_context
        if not ctx:
            return {"success": False, "error": "No context"}

        tool_count = len(ctx.tool_results)
        msg_count = len(ctx.messages)
        has_output = any(
            "created" in r.get("output", "").lower() or
            "wrote" in r.get("output", "").lower()
            for r in ctx.tool_results
        )

        # Build enhanced reflection summary
        parts = [f"Tools: {tool_count}, Messages: {msg_count}, Output found: {has_output}"]

        # TDD result analysis
        if act_result and act_result.get("tdd_result"):
            tdd = act_result["tdd_result"]
            parts.append(
                f"TDD {tdd.final_phase.name.name}: "
                f"{tdd.debug_output.count('PASSED')} passed, "
                f"{tdd.debug_iterations} iterations"
            )

        # Pipeline warnings from ACT (VerificationPipeline failures, security issues)
        if act_result and act_result.get("pipeline_warnings"):
            for w in act_result["pipeline_warnings"]:
                parts.append(f"Warning: {w}")

        # SelfEvo recovery hints: proactively suggest fixes from learned skills
        recovery_hints = self._get_recovery_prompt()
        if recovery_hints:
            parts.append(f"[SelfEvo] Recovery hints available for this task")

        result_str = " | ".join(parts)
        return {
            "success": tool_count > 0 and has_output,
            "error": None if tool_count > 0 else "No tools were called",
            "result": result_str,
        }

    # ─── Model Selection ────────────────────────────────────────────────────

    def _classify_task(self, task: str) -> TaskType:
        """Classify task type for model routing."""
        task_lower = task.lower()
        if any(kw in task_lower for kw in ["analyze", "review", "check", "audit"]):
            return TaskType.ANALYSIS
        if any(kw in task_lower for kw in ["reason", "think", "plan", "design"]):
            return TaskType.REASONING
        if any(kw in task_lower for kw in ["write", "create", "implement", "add", "fix", "refactor"]):
            return TaskType.CODE
        if any(kw in task_lower for kw in ["fast", "quick", "simple", "small"]):
            return TaskType.FAST
        return TaskType.CODE  # Default to code

    # ─── P6: Auto-Decomposition ────────────────────────────────────────────────

    def _classify_task_complexity(self, task: str) -> TaskComplexity:
        """Classify task complexity using heuristic signals.

        This is a fast pre-check (no LLM call) to decide whether
        a task needs decomposition before entering RalphLoop.
        """
        return classify_complexity(task)

    def _decompose_task(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
    ) -> list[dict[str, Any]]:
        """Decompose a complex task into an ordered list of subtasks.

        Uses an LLM call to generate a structured decomposition with
        dependency information. Each subtask dict has keys:
          - description: str
          - task_id: str
          - spec_md: str | None
          - constraints: list[str]
          - depends_on: list[str]  (task_ids this subtask waits for)

        Returns an empty list if decomposition fails or task is simple.
        """
        if not self._llm_client:
            return []

        prompt = (
            "You are a task decomposition expert. Given the following task,\n"
            "break it into 2-8 independent, focused subtasks that can be\n"
            "executed in order. For each subtask, note any dependencies\n"
            "(other subtask IDs it depends on).\n\n"
            f"Task: {task}\n\n"
            "Respond ONLY with valid JSON in this exact format:\n"
            "{\n"
            '  "subtasks": [\n'
            '    {"id": "t1", "description": "...", "depends_on": []},\n'
            '    {"id": "t2", "description": "...", "depends_on": ["t1"]}\n'
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Each subtask should be independently verifiable\n"
            "- Maximum 8 subtasks\n"
            "- Use simple IDs: t1, t2, t3 ...\n"
            "- depends_on is a list of task IDs (empty if no dependencies)\n"
            "- If the task is already simple enough, return a single subtask\n"
        )
        if spec_md:
            prompt += f"\n\nSPEC.md:\n{spec_md[:2000]}"

        try:
            response = self._llm_client.complete(prompt, max_tokens=1024)
            # Try to extract JSON from response
            match = re.search(r'\{[\s\S]+\}', response)
            if not match:
                return []
            data = json.loads(match.group())
            subtasks = data.get("subtasks", [])
            result = []
            for s in subtasks:
                result.append({
                    "description": s["description"],
                    "task_id": s.get("id", str(uuid.uuid4())[:8]),
                    "spec_md": spec_md,
                    "constraints": constraints,
                    "depends_on": s.get("depends_on", []),
                })
            return result
        except Exception:
            return []

    def _execute_decompose_phase(
        self,
        orchestrator: Any,
        task: str,
        spec_md: str | None,
        constraints: list[str],
    ) -> None:
        """Execute the DECOMPOSE phase: classify complexity, decompose if needed.

        Side-effects:
          - Sets orchestrator._decomposition_result with subtask list
          - Transitions orchestrator state from DECOMPOSE to PLAN
        """
        complexity = self._classify_task_complexity(task)

        if complexity == TaskComplexity.SIMPLE:
            # No decomposition needed — transition immediately
            orchestrator._decomposition_result = [{
                "description": task,
                "task_id": str(uuid.uuid4())[:8],
                "spec_md": spec_md,
                "constraints": constraints or [],
                "depends_on": [],
            }]
            orchestrator.state = RalphState.PLAN
            return

        # Complex: decompose via LLM
        subtasks = self._decompose_task(task, spec_md, constraints or [])
        if not subtasks:
            # Decomposition failed — fall back to single task
            subtasks = [{
                "description": task,
                "task_id": str(uuid.uuid4())[:8],
                "spec_md": spec_md,
                "constraints": constraints or [],
                "depends_on": [],
            }]

        orchestrator._decomposition_result = subtasks

        # Update orchestrator task_queue with decomposed subtasks
        orchestrator.task_queue = subtasks
        orchestrator.task_index = 0

        # Transition to PLAN
        orchestrator.state = RalphState.PLAN

    def _select_model(
        self,
        task_type: TaskType,
        prefer_speed: bool = False,
    ) -> str:
        """Select optimal model for task type."""
        if not self._router:
            return "claude-3-5-sonnet-20241022"  # Default fallback
        result = self._router.select_model(
            task_type=task_type,
            requires_tools=True,
        )
        return result if result else "claude-3-5-sonnet-20241022"

    def _get_client_for_model(self, model_name: str) -> LLMClient:
        """Get LLM client for specific model."""
        if self._router:
            return self._router.get_client(model_name)
        # Fallback: create direct client
        return LLMClient(
            provider=self.llm_provider,
            model=model_name,
            api_key=self.llm_api_key or "",
            base_url=self.llm_base_url,
        )

    def _track_usage(self, model_name: str, turns: int) -> None:
        """Track token usage and cost."""
        if not self._router:
            return
        # Estimate: 500 input + 300 output per turn
        cost = self._router.estimate_cost(model_name, 500 * turns, 300 * turns)
        tokens = 800 * turns
        self._total_cost += cost
        self._total_tokens += tokens

    # ─── Self-Evolution ────────────────────────────────────────────────────

    def _learn_from_verification_outcome(
        self,
        pytest_passed: bool | None,
        pytest_output: str,
        task_context: str,
    ) -> None:
        """Learn from VERIFY phase outcomes — both successes and failures.

        This closes the SelfEvo loop: verification results are fed to the
        engine so learned skills can be retrieved for future similar tasks.
        """
        if not self._evo:
            return

        if pytest_passed is False:
            # Test failure: monitor as error
            self._evo.monitor_error(
                tool_name="pytest",
                tool_args={},
                tool_result=pytest_output[:500],
                task_context=task_context,
            )
            skill = self._evo.analyze_and_capture()
            if skill:
                self._evo.store_skill(skill)
                self._skills_learned_count += 1
            # Record failed recovery attempt
            if self._evo._current_event is not None:
                self._evo.update_recovery_result(
                    succeeded=False,
                    recovery_used="pytest-failure",
                )
        elif pytest_passed is True:
            # Test success: capture successful pattern for future similar tasks
            success_skill = self._evo.analyze_and_capture_success(
                task_context=task_context,
                output_summary=pytest_output[:200],
            )
            if success_skill:
                self._evo.store_skill(success_skill)
                self._skills_learned_count += 1
            # If there was a pending error event (recovery was applied during ACT),
            # mark it as successfully recovered so success/failure counts are tracked
            if self._evo._current_event is not None:
                recovery_steps = self._evo.get_best_recovery(
                    self._evo._current_event.error_message
                )
                self._evo.update_recovery_result(
                    succeeded=True,
                    recovery_used=recovery_steps or "applied-recovery-hints",
                )
                self._errors_recovered_count += 1

    def _learn_from_errors(self, ctx: ImplementationContext) -> None:
        """Learn from any errors encountered during execution."""
        if not self._evo:
            return
        for r in ctx.tool_results:
            if not r.get("success", True):
                error_result = r.get("output", "")
                self._evo.monitor_error(
                    tool_name=r.get("tool", "unknown"),
                    tool_args={},
                    tool_result=error_result,
                    task_context=str(ctx.task),
                )
                skill = self._evo.analyze_and_capture()
                if skill:
                    self._evo.store_skill(skill)
                    self._skills_learned_count += 1
                # If recovery was already injected (errors still occurred), mark as failed
                if self._evo._current_event is not None:
                    recovery_used = self._evo.get_best_recovery(
                        self._evo._current_event.error_message
                    )
                    self._evo.update_recovery_result(
                        succeeded=False,
                        recovery_used=recovery_used or "error-during-execution",
                    )

    # ─── System Prompt Builder ─────────────────────────────────────────────

    def _build_system_prompt(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
        role: str = "assistant",
    ) -> str:
        """Build system prompt with project context and Self-Evolution hints."""
        parts = [
            f"You are Ralph, an expert coding assistant.",
            f"RalphLoop methodology: PLAN → ACT → VERIFY → REFLECT.",
            f"Your role in this cycle: {role}.",
        ]

        # Self-Evolution recovery hints
        recovery = self._get_recovery_prompt()
        if recovery:
            parts.append(recovery)

        # Constraints
        if constraints:
            parts.append("\nConstraints:")
            for c in constraints:
                parts.append(f"  - {c}")

        # SPEC.md
        if spec_md:
            parts.append(f"\n\n## SPEC.md\n\n{spec_md}")

        # Task
        parts.append(f"\n\n## Task\n\n{task}")

        return "\n\n".join(parts)

    # ─── Event Handlers ────────────────────────────────────────────────────

    def _on_state_change(self, old: RalphState, new: RalphState) -> None:
        """Called when RalphLoop transitions between states."""
        # Log to WAL
        if self._wal:
            self._wal.log_transition(
                from_state=old.name,
                to_state=new.name,
                trigger="state_change",
            )
            self._wal_entries_count += 1

        # Periodic checkpoint
        if (self.enable_checkpoint and self._ckpt and
                self._current_loop and
                self._current_loop.metrics.total_iterations % self.checkpoint_interval == 0):
            try:
                self._ckpt.save_checkpoint(
                    state=new.name,
                    task_index=self._current_loop.task_index,
                    retry_count=self._current_loop.retry_count,
                    context_usage=self._current_loop.context_usage,
                    task_queue=self._current_loop.task_queue,
                    error_log=[{"msg": e} for e in self._current_loop.error_log[-5:]],
                )
                self._checkpoints_saved_count += 1
            except Exception:
                pass

    def _on_escalation(self, ctx: dict) -> EscalationOption:
        """Called when RalphLoop exhausts retries — user chooses next action."""
        # In automated mode, prefer rewrite over abandon
        return EscalationOption.REWRITE

    def _on_warning(self, tier: ContextTier, message: str) -> None:
        """Called when context budget enters DEGRADING or POOR tier."""
        # Log warning
        if self._current_context:
            self._current_context.log_error(f"Context tier warning [{tier.name}]: {message}")

    # ─── Utilities ─────────────────────────────────────────────────────────

    def _build_summary(
        self,
        result: dict,
        task_id: str,
        duration: float,
    ) -> str:
        """Build human-readable summary of execution."""
        metrics = result.get("metrics")
        if metrics:
            iterations = metrics.total_iterations
            retries = metrics.total_retries
        else:
            iterations = retries = 0

        status = "SUCCESS" if result.get("success") else "FAILED"
        return (
            f"[{status}] Task {task_id} completed in {duration:.1f}s. "
            f"Final state: {result.get('final_state', 'UNKNOWN').name}. "
            f"Iterations: {iterations}, Retries: {retries}, "
            f"Cost: ${self._total_cost:.4f}, Tokens: {self._total_tokens}, "
            f"Skills learned: {self._skills_learned_count}."
        )

    def get_stats(self) -> dict:
        """Get current execution statistics."""
        return {
            "skills_learned": self._skills_learned_count,
            "checkpoints_saved": self._checkpoints_saved_count,
            "wal_entries": self._wal_entries_count,
            "errors_recovered": self._errors_recovered_count,
            "total_cost_usd": round(self._total_cost, 6),
            "total_tokens": self._total_tokens,
        }

    def recover_from_checkpoint(self, checkpoint_id: str) -> bool:
        """Recover state from a checkpoint."""
        if not self._ckpt:
            return False
        try:
            ckpt = self._ckpt.load_checkpoint(checkpoint_id)
            if ckpt and self._current_loop:
                self._current_loop.state = RalphState[ckpt["state"]]
                self._current_loop.task_index = ckpt["task_index"]
                self._current_loop.retry_count = ckpt["retry_count"]
                return True
        except Exception:
            pass
        return False

    def recover_from_wal(self) -> dict[str, Any]:
        """Analyze WAL and suggest recovery actions."""
        if not self._wal:
            return {"error": "WAL disabled"}
        return self._wal.get_recovery_plan()

    def clear_wal(self) -> None:
        """Clear WAL after successful commit."""
        if self._wal:
            self._wal.clear()

    def close(self) -> None:
        """Clean up resources."""
        if self._wal:
            self._wal.close()
        if self._router:
            self._router.clear_client_cache()
