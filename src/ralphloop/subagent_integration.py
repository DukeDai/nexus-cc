"""RalphLoop + Subagent Integration — Orchestrate multi-agent collaboration.

This module bridges RalphLoop (state machine orchestrator) with the
delegate_task subagent system, enabling parallel专业化 agents.

Key innovation vs Claude Code:
- Claude Code: single agent, no parallelism, no role specialization
- RalphLoop: RalphLoop orchestrates → multiple specialized subagents in parallel

Architecture:
    RalphLoop ACT state
        ├── SpecifierAgent (parallel)
        ├── ImplementerAgent (main, sequential within TDD cycle)
        ├── ReviewerAgent (parallel with Implementer)
        ├── SecurityAgent (parallel with Implementer)
        └── TestAgent (on-demand)

Result aggregation → RalphLoop REFLECT → decision (commit/retry/escalate)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .subagent_registry import (
    SUBAGENT_DEFINITIONS,
    get_subagent,
    get_all_subagents,
    SubagentDefinition,
)
from .claude_md_loader import (
    ProjectContext,
    load_claude_md,
    find_project_root,
    build_llm_system_prompt,
)
from .implementation_context import ImplementationContext


# ─── Result Types ─────────────────────────────────────────────────────────────

@dataclass
class SubagentResult:
    """Result from a single subagent execution.
    
    Attributes:
        task_id: Unique identifier for this task
        role: Subagent role (specifier, implementer, reviewer, etc.)
        status: Execution status (complete, error, timeout, escalate)
        files_created: List of files created by this subagent
        files_modified: List of files modified by this subagent
        tool_calls: Number of tool calls made
        turns: Number of LLM turns used
        output: Raw output from subagent
        summary: Human-readable summary
        escalate_reason: If status=escalate, why
        duration_seconds: How long this subagent ran
        model_used: Which model was used
        cost_tokens: Approximate token cost (if available)
    """
    task_id: str
    role: str
    status: str  # "complete" | "error" | "timeout" | "escalate"
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tool_calls: int = 0
    turns: int = 0
    output: str = ""
    summary: str = ""
    escalate_reason: Optional[str] = None
    duration_seconds: float = 0.0
    model_used: Optional[str] = None
    cost_tokens: Optional[int] = None
    
    def is_success(self) -> bool:
        return self.status == "complete"
    
    def is_escalate(self) -> bool:
        return self.status == "escalate"


@dataclass
class OrchestratedResult:
    """Result from RalphLoop orchestrator after subagent execution.
    
    Attributes:
        task_id: Overall task identifier
        primary: Primary implementer result
        parallel_results: Results from parallel agents (reviewer, security, etc.)
        spec_result: Specifier result if run
        overall_status: "success" | "partial" | "failed" | "escalated"
        decisions: List of RalphLoop decisions made
        context: Updated implementation context
        total_duration_seconds: End-to-end duration
    """
    task_id: str
    overall_status: str  # "success" | "partial" | "failed" | "escalated"
    primary: Optional[SubagentResult] = None
    spec_result: Optional[SubagentResult] = None
    parallel_results: list[SubagentResult] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    context: Optional[ImplementationContext] = None
    total_duration_seconds: float = 0.0
    summary: str = ""
    
    @property
    def all_results(self) -> list[SubagentResult]:
        results = []
        if self.spec_result:
            results.append(self.spec_result)
        if self.primary:
            results.append(self.primary)
        results.extend(self.parallel_results)
        return results
    
    def has_failures(self) -> bool:
        return any(r.status != "complete" for r in self.all_results)
    
    def escalation_needed(self) -> bool:
        return any(r.is_escalate() for r in self.all_results)


# ─── Subagent Integration ─────────────────────────────────────────────────────

class SubagentIntegration:
    """Bridge between RalphLoop orchestrator and delegate_task subagents.
    
    This class:
    1. Prepares subagent tasks with proper context
    2. Launches parallel subagents via delegate_task
    3. Collects and aggregates results
    4. Reports back to RalphLoop orchestrator
    """
    
    def __init__(
        self,
        workdir: Path | str | None = None,
        llm_client: Any = None,
        model_router: Any = None,
        model_preference: str = "auto",
    ):
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.llm_client = llm_client
        self.model_router = model_router
        self.model_preference = model_preference
        self._project_ctx = ProjectContext(self.workdir)
    
    # ─── Public API ──────────────────────────────────────────────────────────
    
    def run_implementer_with_review(
        self,
        task: str,
        spec_md: str | None = None,
        constraints: list[str] | None = None,
        max_turns: int = 15,
        enable_tdd: bool = False,
    ) -> OrchestratedResult:
        """Run ImplementerAgent with ReviewerAgent in parallel.
        
        This is the core RalphLoop ACT pattern:
            ImplementerAgent (code + TDD when enabled) ─┬─→ RalphLoop REFLECT
            ReviewerAgent (review)                      ─┘
        
        Args:
            task: The implementation task description
            spec_md: Optional SPEC.md content
            constraints: List of constraints (security, performance, etc.)
            max_turns: Max LLM turns for implementer
            enable_tdd: If True, run ImplementerAgent with TDDEnforcer (RED→GREEN→REFACTOR).
                       When disabled, ImplementerAgent just implements directly.
        
        Returns:
            OrchestratedResult with primary + parallel results
        """
        task_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        decisions = []
        
        # Build context for subagents
        system_prompt = self._build_system_prompt(task, spec_md, constraints)
        
        # ── Parallel execution: Implementer + Reviewer ──
        parallel_results: list[SubagentResult] = []
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def run_implementer() -> SubagentResult:
            start = time.time()
            impl_def = get_subagent("implementer")
            if impl_def is None:
                return SubagentResult(task_id=task_id, role="implementer", status="error",
                                     output="ImplementerAgent not found", duration_seconds=time.time()-start)
            # Run via run_agent_loop
            try:
                impl_context = ImplementationContext(task=task, messages=[], tool_results=[], test_results=[], error_log=[])
                from .agent_loop import run_agent_loop, AgentLoopConfig
                impl_config = AgentLoopConfig(max_turns=max_turns)
                result = run_agent_loop(
                    task=self._build_implementer_goal(task, spec_md, constraints),
                    llm_client=self._get_llm_client(),
                    context=impl_context,
                    config=impl_config,
                    workdir=self.project_root,
                    tools=self._get_tools(),
                )
                
                # ── TDD Enforcement (when enabled) ──────────────────────────────
                # After initial implementation, run TDD cycle if requested.
                # This enforces RED→GREEN→REFACTOR before considering ACT complete.
                tdd_result = None
                if enable_tdd:
                    try:
                        from .tdd_enforcer import TDDEnforcer
                        tdd = TDDEnforcer()
                        tdd_result = tdd.run_cycle(
                            llm_client=self._get_llm_client(),
                            messages=list(impl_context.messages),
                            task=task,
                        )
                        if not tdd_result.success:
                            # TDD failure → ACT failure (ESCALATE)
                            return SubagentResult(
                                task_id=task_id, role="implementer", status="error",
                                tool_calls=result.tool_calls, turns=result.turns,
                                output=f"TDD {tdd_result.final_phase.name} failed: {tdd_result.final_test_output[:200]}",
                                summary=f"TDD {tdd_result.final_phase.name}: {tdd_result.debug_output[:100]}",
                                duration_seconds=time.time()-start,
                            )
                    except Exception as e:
                        return SubagentResult(task_id=task_id, role="implementer", status="error",
                                             output=f"TDD error: {e}", duration_seconds=time.time()-start)
                
                return SubagentResult(
                    task_id=task_id, role="implementer", status="complete" if result.complete else "error",
                    tool_calls=result.tool_calls, turns=result.turns,
                    output=result.final_content, summary=result.final_content[:200],
                    duration_seconds=time.time()-start,
                )
            except Exception as e:
                return SubagentResult(task_id=task_id, role="implementer", status="error",
                                     output=str(e), duration_seconds=time.time()-start)

        def run_reviewer() -> SubagentResult:
            start = time.time()
            rev_def = get_subagent("reviewer")
            if rev_def is None:
                return SubagentResult(task_id=task_id, role="reviewer", status="error",
                                     output="ReviewerAgent not found", duration_seconds=time.time()-start)
            try:
                rev_context = ImplementationContext(task=task, messages=[], tool_results=[], test_results=[], error_log=[])
                from .agent_loop import run_agent_loop, AgentLoopConfig
                rev_config = AgentLoopConfig(max_turns=10)
                result = run_agent_loop(
                    task=self._build_reviewer_goal(task, spec_md),
                    llm_client=self._get_llm_client(),
                    context=rev_context,
                    config=rev_config,
                    workdir=self.project_root,
                    tools=self._get_tools(),
                )
                return SubagentResult(
                    task_id=task_id, role="reviewer", status="complete" if result.complete else "error",
                    tool_calls=result.tool_calls, turns=result.turns,
                    output=result.final_content, summary=result.final_content[:200],
                    duration_seconds=time.time()-start,
                )
            except Exception as e:
                return SubagentResult(task_id=task_id, role="reviewer", status="error",
                                     output=str(e), duration_seconds=time.time()-start)

        # Execute implementer and reviewer in parallel
        decisions.append("PARALLEL_MODE: Implementer + Reviewer running concurrently")
        with ThreadPoolExecutor(max_workers=2) as pool:
            impl_future = pool.submit(run_implementer)
            rev_future = pool.submit(run_reviewer)
            for completed in as_completed([impl_future, rev_future]):
                result = completed.result()
                parallel_results.append(result)
                decisions.append(f"{result.role.upper()}: {result.status} ({result.duration_seconds:.1f}s)")

        impl_result = next((r for r in parallel_results if r.role == "implementer"), None)
        rev_result = next((r for r in parallel_results if r.role == "reviewer"), None)

        # Aggregate results
        overall = "success" if (impl_result and impl_result.is_success()) else "partial"
        return OrchestratedResult(
            task_id=task_id,
            primary=impl_result,
            parallel_results=[rev_result] if rev_result else [],
            overall_status=overall,
            decisions=decisions,
            total_duration_seconds=time.time() - start_time,
            summary=f"Implementer: {impl_result.status if impl_result else 'N/A'}, Reviewer: {rev_result.status if rev_result else 'N/A'}",
        )
    
    def run_specifier(self, raw_requirements: str) -> SubagentResult:
        """Run SpecifierAgent to generate SPEC.md from raw requirements.
        
        Args:
            raw_requirements: User's raw task description
            
        Returns:
            SubagentResult with spec_md in output
        """
        task_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        spec_def = get_subagent("specifier")
        if spec_def is None:
            return SubagentResult(
                task_id=task_id,
                role="specifier",
                status="error",
                output="SpecifierAgent not found in registry",
                summary="Failed: specifier subagent not registered",
                duration_seconds=time.time() - start_time,
            )
        
        # Build goal
        goal = f"""Analyze and clarify this requirements:

{raw_requirements}

Generate a SPEC.md that:
1. Clearly defines the functionality
2. Lists user interactions
3. Describes data flow
4. Provides VERIFIABLE acceptance criteria
5. Identifies edge cases and error conditions

Return the complete SPEC.md content."""
        
        system_prompt = self._build_system_prompt(
            task=f"SPEC generation for: {raw_requirements[:200]}",
            spec_md=None,
            constraints=["Follow SPEC.md template format"],
        )
        
        # Run via run_agent_loop
        try:
            spec_context = ImplementationContext(task=goal, messages=[], tool_results=[], test_results=[], error_log=[])
            from .agent_loop import run_agent_loop, AgentLoopConfig
            spec_config = AgentLoopConfig(max_turns=spec_def.max_turns)
            result = run_agent_loop(
                task=goal,
                llm_client=self._get_llm_client(),
                context=spec_context,
                config=spec_config,
                workdir=self.project_root,
                tools=self._get_tools(),
            )
            return SubagentResult(
                task_id=task_id,
                role="specifier",
                status="complete" if result.complete else "error",
                tool_calls=result.tool_calls,
                turns=result.turns,
                output=result.final_content,
                summary=result.final_content[:200] if result.final_content else "No output",
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            return SubagentResult(
                task_id=task_id,
                role="specifier",
                status="error",
                output=str(e),
                summary=f"Failed: {str(e)[:100]}",
                duration_seconds=time.time() - start_time,
            )
    
    def run_security_scan(self, files: list[str]) -> SubagentResult:
        """Run SecurityAgent to scan files for vulnerabilities.
        
        Args:
            files: List of file paths to scan
            
        Returns:
            SubagentResult with security findings
        """
        task_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        sec_def = get_subagent("security")
        if sec_def is None:
            return SubagentResult(
                task_id=task_id,
                role="security",
                status="error",
                output="SecurityAgent not found",
                summary="Failed: security subagent not registered",
                duration_seconds=time.time() - start_time,
            )
        
        # Build goal for security scan
        file_list = "\n".join([f"- {f}" for f in files[:20]])
        goal = f"""Scan these files for security vulnerabilities:

{file_list}
{f'{len(files) - 20} more files...' if len(files) > 20 else ''}

Provide a JSON security report with:
- vulnerabilities: list of found issues
- summary: brief summary
- safe_to_deploy: true/false
"""
        
        # Run via run_agent_loop
        try:
            sec_context = ImplementationContext(task=goal, messages=[], tool_results=[], test_results=[], error_log=[])
            from .agent_loop import run_agent_loop, AgentLoopConfig
            sec_config = AgentLoopConfig(max_turns=sec_def.max_turns)
            result = run_agent_loop(
                task=goal,
                llm_client=self._get_llm_client(),
                context=sec_context,
                config=sec_config,
                workdir=self.project_root,
                tools=self._get_tools(),
            )
            return SubagentResult(
                task_id=task_id,
                role="security",
                status="complete" if result.complete else "error",
                tool_calls=result.tool_calls,
                turns=result.turns,
                output=result.final_content,
                summary=result.final_content[:200] if result.final_content else "No output",
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            return SubagentResult(
                task_id=task_id,
                role="security",
                status="error",
                output=str(e),
                summary=f"Failed: {str(e)[:100]}",
                duration_seconds=time.time() - start_time,
            )
    
    def aggregate_results(self, results: list[SubagentResult]) -> dict:
        """Aggregate multiple subagent results into a summary.
        
        Args:
            results: List of SubagentResult
            
        Returns:
            Summary dict with file changes, issues, security findings
        """
        all_files_created: set[str] = set()
        all_files_modified: set[str] = set()
        all_tool_calls = 0
        all_turns = 0
        escalations = []
        
        for r in results:
            all_files_created.update(r.files_created)
            all_files_modified.update(r.files_modified)
            all_tool_calls += r.tool_calls
            all_turns += r.turns
            if r.is_escalate():
                escalations.append(r.escalate_reason)
        
        return {
            "total_files_created": len(all_files_created),
            "total_files_modified": len(all_files_modified),
            "files_created": sorted(all_files_created),
            "files_modified": sorted(all_files_modified),
            "total_tool_calls": all_tool_calls,
            "total_turns": all_turns,
            "escalations": escalations,
            "has_escalations": len(escalations) > 0,
        }
    
    # ─── Private Helpers ──────────────────────────────────────────────────────
    
    def _build_system_prompt(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str] | None,
    ) -> str:
        """Build system prompt with project context."""
        parts = []
        
        # RalphLoop role
        parts.append("""You are Ralph — an expert coding assistant following RalphLoop methodology.
RalphLoop: PLAN → ACT → VERIFY → REFLECT → (COMMIT|RETRY|ESCALATE)

Your job is to help implement code. Be precise, follow TDD, and use tools wisely.""")
        
        # Project context
        proj_prompt = build_llm_system_prompt(self.workdir)
        if proj_prompt:
            parts.append("\n# Project Context\n")
            parts.append(proj_prompt)
        
        # CLAUDE.md if exists
        claude_md = load_claude_md(self.workdir)
        if claude_md:
            parts.append("\n# CLAUDE.md (Project Rules)\n")
            parts.append(claude_md)
        
        # Constraints
        if constraints:
            parts.append("\n# Constraints\n")
            for c in constraints:
                parts.append(f"- {c}")
        
        # SPEC.md
        if spec_md:
            parts.append("\n# SPEC.md\n")
            parts.append(spec_md)
        
        # Task
        parts.append("\n# Task\n")
        parts.append(task)
        
        return "\n".join(parts)
    
    @property
    def project_root(self) -> Path:
        """Lazily detect project root using find_project_root."""
        return find_project_root(self.workdir) or self.workdir
    
    def _get_llm_client(self, task_type: str = "code") -> Any:
        """Get or create LLM client for subagent execution.
        
        Uses ModelRouter to select the optimal model based on task_type,
        falling back to a default client if no router is configured.
        
        Task types: "code" | "analysis" | "reasoning" | "fast"
        """
        # Priority 1: use the already-configured client (RalphLoop's client with MiniMax)
        if self.llm_client:
            return self.llm_client
        
        # Priority 2: use the model_router if available
        if self.model_router:
            try:
                from ..llm.model_router import TaskType
                # Map subagent task types to ModelRouter TaskType
                task_type_map = {
                    "code": TaskType.CODE,
                    "analysis": TaskType.REASONING,
                    "reasoning": TaskType.REASONING,
                    "fast": TaskType.FAST,
                }
                nexus_tt = task_type_map.get(task_type, TaskType.CODE)
                model_name = self.model_router.select_model(nexus_tt)
                config = self.model_router.models[model_name]
                api_key = self.model_router.api_keys.get(config.provider, "")
                base_url = self.model_router.base_urls.get(config.provider, "")
                from ..llm.client import LLMClient
                return LLMClient(
                    provider=config.provider,
                    model=model_name,
                    api_key=api_key,
                    base_url=base_url,
                )
            except Exception:
                pass
        
        # Priority 3: fallback — detect credentials and create client
        import os, json
        from pathlib import Path
        settings_path = Path.home() / ".claude" / "settings.json"
        settings_env = {}
        if settings_path.exists():
            try:
                settings_env = json.loads(settings_path.read_text()).get("env", {})
            except Exception:
                pass
        
        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY", "") or settings_env.get("ANTHROPIC_AUTH_TOKEN", "")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "") or settings_env.get("ANTHROPIC_BASE_URL", "")
        
        from ..llm.client import LLMClient, Provider
        return LLMClient(provider=Provider.ANTHROPIC, model="claude-sonnet-4-20250514", api_key=api_key, base_url=base_url)

    def _get_tools(self) -> list[dict]:
        """Get tool definitions for subagent execution."""
        from .agent_loop import TOOL_DEFINITIONS
        return TOOL_DEFINITIONS

    def _build_reviewer_goal(self, task: str, spec_md: str | None) -> str:
        """Build the goal for ReviewerAgent."""
        parts = [f"Review the implementation for this task:\n\n{task}\n"]
        if spec_md:
            parts.append(f"\n\nSPEC.md to verify against:\n{spec_md}")
        parts.append("""
Review the code quality:
- Correctness: Does it match the spec?
- Security: Any vulnerabilities?
- Performance: Any obvious issues?
- Tests: Are edge cases covered?

Use tools to read files and inspect code.
Report findings concisely.
""")
        return "".join(parts)

    def _build_implementer_goal(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str] | None,
    ) -> str:
        """Build the goal for ImplementerAgent."""
        parts = [f"Implement the following task:\n\n{task}\n"]
        
        if spec_md:
            parts.append(f"\n\nFollow this SPEC.md:\n{spec_md}")
        
        if constraints:
            parts.append("\n\nConstraints:")
            for c in constraints:
                parts.append(f"\n- {c}")
        
        parts.append("""

Follow TDD methodology:
1. Write RED test first (test that defines expected behavior)
2. Write GREEN implementation (minimal code to pass test)
3. REFACTOR to improve quality

Use tools: read_file, write_file, apply_diff, bash (pytest).

After implementing:
1. Run tests and confirm all pass
2. Summarize what was done
3. List files created/modified
""")
        
        return "\n".join(parts)


# ─── RalphLoop Orchestrator Enhancement ───────────────────────────────────────

def orchestrate_with_subagents(
    task: str,
    workdir: Path | str | None = None,
    mode: str = "implementer_with_review",
    spec_md: str | None = None,
    constraints: list[str] | None = None,
) -> OrchestratedResult:
    """High-level entry point: run RalphLoop with subagent orchestration.
    
    Args:
        task: Task description
        workdir: Working directory
        mode: "specifier_only" | "implementer_with_review" | "full"
        spec_md: Optional pre-generated SPEC.md
        constraints: Optional list of constraints
    
    Returns:
        OrchestratedResult with all subagent results aggregated
    """
    integration = SubagentIntegration(workdir=workdir)
    
    if mode == "specifier_only":
        spec_result = integration.run_specifier(task)
        return OrchestratedResult(
            task_id=str(uuid.uuid4())[:8],
            overall_status="success" if spec_result.is_success() else "failed",
            spec_result=spec_result,
            summary=spec_result.summary,
        )
    
    elif mode == "implementer_with_review":
        return integration.run_implementer_with_review(
            task=task,
            spec_md=spec_md,
            constraints=constraints,
        )
    
    elif mode == "full":
        # Specifier → Implementer + Reviewer + Security (parallel)
        spec_result = integration.run_specifier(task)
        
        if not spec_result.is_success():
            return OrchestratedResult(
                task_id=str(uuid.uuid4())[:8],
                overall_status="failed",
                spec_result=spec_result,
                summary=f"Spec generation failed: {spec_result.output}",
            )
        
        # Now run implementer with parallel review
        impl_result = integration.run_implementer_with_review(
            task=task,
            spec_md=spec_result.output,
            constraints=constraints,
        )
        
        return impl_result
    
    else:
        raise ValueError(f"Unknown mode: {mode}")
