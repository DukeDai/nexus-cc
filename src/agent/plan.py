"""Plan data model - first-class artifact for plan-first execution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agents.base import AgentRole


class PlanStepKind(str, Enum):
    TOOL = "tool"
    VERIFY = "verify"
    CRITIQUE = "critique"
    ASK_USER = "ask_user"
    SUBPLAN = "subplan"


class OnFailure(str, Enum):
    ABORT = "abort"
    SKIP = "skip"
    RETRY = "retry"
    ASK = "ask"
    RETRY_WITH_FEEDBACK = "retry_with_feedback"


@dataclass
class PlanStep:
    id: str
    kind: PlanStepKind
    intent: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    role: "AgentRole | None" = None
    subplan_args: dict[str, Any] | None = None
    pipeline: str | None = None
    pipeline_args: dict[str, Any] | None = None
    success_criteria: str = ""
    on_failure: OnFailure = OnFailure.ASK
    timeout_s: int = 120


@dataclass
class Plan:
    plan_id: str
    spec: str
    steps: list[PlanStep] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["steps"] = [
            {**asdict(s), "kind": s.kind.value, "on_failure": s.on_failure.value}
            for s in self.steps
        ]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        steps = [
            PlanStep(
                id=s["id"],
                kind=PlanStepKind(s["kind"]),
                intent=s["intent"],
                tool=s.get("tool"),
                args=s.get("args", {}),
                success_criteria=s.get("success_criteria", ""),
                on_failure=OnFailure(s.get("on_failure", "ask")),
                timeout_s=s.get("timeout_s", 120),
            )
            for s in d.get("steps", [])
        ]
        return cls(
            plan_id=d["plan_id"],
            spec=d["spec"],
            steps=steps,
            assumptions=d.get("assumptions", []),
            risks=d.get("risks", []),
            created_at=datetime.fromisoformat(d["created_at"]),
            version=d.get("version", 1),
        )

    def find_step(self, step_id: str) -> PlanStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None


def new_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:8]}"


def new_step_id() -> str:
    return f"step_{uuid.uuid4().hex[:8]}"