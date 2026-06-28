"""Self-evolution engine coordinator.

Thin wrapper over src/engine/self_evolution.py + feedback_loop_integration.py.
Reads WAL patterns after each walk, stages prompt updates for user approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.agent.plan import Plan
from src.agent.control import StepResult
from src.agent.prompts import PromptTemplate


@dataclass
class StagedChanges:
    """Evolver-produced prompt updates pending user approval."""

    changes: dict[str, PromptTemplate]    # template_name → proposed new version
    rationale: dict[str, str]            # template_name → why
    created_at: datetime = field(default_factory=datetime.now)


class Evolver:
    """Coordinator for the closed-loop self-evolution feedback system."""

    # Churn cap: each (template_name, version) updates at most once per N walks.
    WALK_COUNT_CAP = 5

    def __init__(self, wal: Any, memory: Any, feedback: Any):
        self._wal = wal
        self._memory = memory
        self._feedback = feedback
        self._last_outcome: dict[str, Any] = {}
        self._walk_count: int = 0

    def record_outcome(self, plan: Plan, results: list[StepResult]) -> None:
        """Extract error histograms, retry rates, planner failures from results."""
        self._walk_count += 1
        histogram: dict[str, int] = {}
        failed = 0
        for r in results:
            if r.status == "failed":
                failed += 1
                cat = getattr(r, "error_category", None) or "unknown"
                histogram[cat] = histogram.get(cat, 0) + 1
        self._last_outcome = {
            "plan_id": plan.plan_id,
            "total_steps": len(plan.steps),
            "failed_count": failed,
            "error_histogram": histogram,
            "walk_count": self._walk_count,
        }

    def update_prompt_registry(
        self, registry: "PromptTemplateRegistry"
    ) -> StagedChanges:
        """Stage prompt updates based on last_outcome heuristics.

        Heuristic: if failure rate > 30% and walk count delta > cap, propose
        augmenting the planner system prompt with observed error categories.
        """
        outcome = self._last_outcome
        total = outcome.get("total_steps", 0)
        failed = outcome.get("failed_count", 0)
        if total == 0:
            return StagedChanges(changes={}, rationale={})

        failure_rate = failed / total
        changes: dict[str, PromptTemplate] = {}
        rationale: dict[str, str] = {}

        if failure_rate > 0.3:
            histogram = outcome.get("error_histogram", {})
            if histogram:
                try:
                    current = registry.get("planner")
                except KeyError:
                    return StagedChanges(changes={}, rationale={})
                walks_since_update = self._walk_count - current.last_updated_walk_count
                if walks_since_update < self.WALK_COUNT_CAP:
                    return StagedChanges(changes={}, rationale={})
                error_summary = ", ".join(f"{cat}={n}" for cat, n in histogram.items())
                new_prompt = (
                    current.system_prompt
                    + f"\n\n# Recent error patterns\nAvoid these: {error_summary}"
                )
                new_template = PromptTemplate(
                    name="planner",
                    system_prompt=new_prompt,
                    version=current.version + 1,
                    updated_at=datetime.now(),
                    source_episodes=[outcome.get("plan_id", "")],
                    last_updated_walk_count=self._walk_count,
                )
                changes["planner"] = new_template
                rationale["planner"] = (
                    f"Failure rate {failure_rate:.0%} exceeds 30% threshold; "
                    f"adding observed error patterns to planner prompt"
                )

        return StagedChanges(changes=changes, rationale=rationale)

    def should_replan(self, partial_results: list[StepResult]) -> bool:
        """Decide mid-walk whether the plan should be regenerated."""
        if not partial_results:
            return False
        recent_failures = sum(1 for r in partial_results[-3:] if r.status == "failed")
        return recent_failures >= 3