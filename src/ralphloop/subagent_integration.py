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
        model_preference: str = "auto",
    ):
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.llm_client = llm_client
        self.model_preference = model_preference
        self._project_ctx = ProjectContext(self.workdir)
    
    # ─── Public API ──────────────────────────────────────────────────────────
    
    def run_implementer_with_review(
        self,
        task: str,
        spec_md: str | None = None,
        constraints: list[str] | None = None,
        max_turns: int = 15,
    ) -> OrchestratedResult:
        """Run ImplementerAgent with ReviewerAgent in parallel.
        
        This is the core RalphLoop ACT pattern:
            ImplementerAgent (code + TDD)  ─┬─→ RalphLoop REFLECT
            ReviewerAgent (review)          ─┘
        
        Args:
            task: The implementation task description
            spec_md: Optional SPEC.md content
            constraints: List of constraints (security, performance, etc.)
            max_turns: Max LLM turns for implementer
        
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
        
        # TODO: Actually launch via delegate_task when available
        # For now, implement the orchestration logic
        # 
        # Future (with delegate_task):
        # impl_future = delegate_task(
        #     goal=self._build_implementer_goal(task, spec_md, constraints),
        #     context=system_prompt,
        #     tasks=[{
        #         "goal": f"Implement: {task}",
        #         "context": system_prompt,
        #         "toolsets": ["terminal", "file"],
        #         "role": "implementer",
        #     }]
        # )
        #
        # review_future = delegate_task(
        #     goal=f"Review code for: {task}",
        #     context=system_prompt,
        #     tasks=[{
        #         "goal": f"Code review: {task}",
        #         "context": system_prompt,
        #         "toolsets": ["terminal", "file"],
        #         "role": "reviewer",
        #     }]
        # )
        
        # ── Fallback: sequential (when delegate_task not configured) ──
        # This path is for development/testing before subagent system is live
        decisions.append("SEQUENTIAL_MODE: delegate_task not configured, using sequential")
        
        return OrchestratedResult(
            task_id=task_id,
            overall_status="partial",
            decisions=decisions,
            total_duration_seconds=time.time() - start_time,
            summary=f"SubagentIntegration.run_implementer_with_review: delegate_task integration pending. Task: {task[:100]}",
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
        
        # TODO: Replace with actual delegate_task call
        return SubagentResult(
            task_id=task_id,
            role="specifier",
            status="complete",
            output=f"SpecifierAgent would generate SPEC.md for: {raw_requirements[:200]}...",
            summary=f"SpecifierAgent ready. Would generate SPEC.md.",
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
        
        return SubagentResult(
            task_id=task_id,
            role="security",
            status="complete",
            output=f"SecurityAgent would scan: {', '.join(files[:5])}",
            summary=f"SecurityAgent ready. Would scan {len(files)} files.",
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
