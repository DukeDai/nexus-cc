"""Bridge between VERIFY step and src/verification/pipeline.py.

VerificationAdapter allows the PlanWalker to invoke named verification
pipelines (security, tdd, test, review) without coupling to their
concrete implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from src.agent.control import StepResult
from src.agent.plan import PlanStep

if TYPE_CHECKING:
    from src.context.wal import WALManager


class VerificationOutcome:
    """Outcome of a verification pipeline run."""

    def __init__(
        self,
        passed: bool,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.passed = passed
        self.errors = errors or []
        self.warnings = warnings or []
        self.metadata = metadata or {}


class VerificationPipeline(Protocol):
    """Protocol that all verification pipelines implement."""

    async def verify(
        self,
        step: PlanStep,
        step_result: StepResult,
        ctx: dict[str, Any],
    ) -> VerificationOutcome: ...


class VerificationAdapter:
    """Registry + dispatcher for verification pipelines."""

    def __init__(self, wal: "WALManager"):
        self._wal = wal
        self._pipelines: dict[str, VerificationPipeline] = {}

    def register(self, name: str, pipeline: VerificationPipeline) -> None:
        if name in self._pipelines:
            raise ValueError(f"Pipeline {name!r} already registered")
        self._pipelines[name] = pipeline

    def list_pipelines(self) -> list[str]:
        return list(self._pipelines.keys())

    async def run(
        self,
        step: PlanStep,
        step_result: StepResult,
        ctx: dict[str, Any],
    ) -> VerificationOutcome:
        if step.pipeline is None:
            raise ValueError(f"VERIFY step {step.id} has no pipeline")
        if step.pipeline not in self._pipelines:
            raise KeyError(f"Pipeline {step.pipeline!r} not registered")
        # Provide WAL context: which tools ran, which files touched, what was edited.
        wal_context = self._wal.context_for_step(step.id) if hasattr(self._wal, "context_for_step") else {}
        merged_ctx = {**ctx, "wal": wal_context, "pipeline_args": step.pipeline_args or {}}
        return await self._pipelines[step.pipeline].verify(step, step_result, merged_ctx)