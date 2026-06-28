"""Bridge between VERIFY step and src/verification/pipeline.py.

VerificationAdapter allows the PlanWalker to invoke named verification
pipelines (security, tdd, test, review) without coupling to their
concrete implementations.

v1.2 wiring: the ReviewGate delegate functions (spec_compliance,
logic_analysis, security_review) accept a ``model_hint`` parameter and
forward it to the underlying LLM as ``model_hint=...``. When
``NEXUS_USE_MODEL_ROUTER=1`` the runtime's ``_RouterAdapter`` consumes
that kwarg and routes via ModelRouter. With the flag unset the legacy
``LLMClient`` absorbs the kwarg into ``**kwargs`` — behavior unchanged.

The delegate functions below are the v1.2 wiring point for the
ReviewGate / SecurityScan LLM paths. They live here (rather than in
src/verification/*) so the agent side owns the ModelHint policy and
verification gates remain call-shape compatible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from src.agent.control import StepResult
from src.agent.plan import PlanStep
from src.llm.model_policy import ModelHint

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


# --------------------------------------------------------------------- delegates
#
# ReviewGate delegate callables. Each takes (task_description, context)
# and returns the dict the gate expects ({"success": ..., "result": ...}).
# The default ``model_hint`` is VERIFIER_REVIEW; the SecurityScan stub
# uses VERIFIER_SECURITY (per v1.2 cost-downgrade decision).
#
# When ``llm`` is None (the legacy default) delegates return a benign
# no-op dict so the rest of the pipeline can run unchanged.


def _extract_response_text(response: Any) -> str:
    """Pull a plain string out of an LLM response, falling back gracefully."""
    content = getattr(response, "content", None)
    if not content:
        return ""
    first = content[0]
    text = getattr(first, "text", None)
    return text if isinstance(text, str) else str(first)


async def _delegate_spec_compliance(
    task: str,
    ctx: dict[str, Any],
    *,
    llm: Any | None = None,
    model_hint: ModelHint = ModelHint.VERIFIER_REVIEW,
) -> dict[str, Any]:
    """ReviewGate spec-compliance delegate.

    Forwards the task + spec context to the LLM under ``model_hint`` and
    returns the gate's expected ``{"success": bool, "result": list|str}``.
    When ``llm`` is None the delegate returns an empty result (the gate
    treats this as "no issues found").
    """
    if llm is None:
        return {"success": True, "result": []}

    user_msg = (
        f"{task}\n\n"
        f"Spec: {ctx.get('spec')!r}\n"
        f"File: {ctx.get('file_path')!r}"
    )
    response = await llm.complete(
        system="You are an independent code reviewer. Return a JSON array of issues.",
        messages=[{"role": "user", "content": user_msg}],
        model_hint=model_hint,
    )
    return {"success": True, "result": _extract_response_text(response)}


async def _delegate_logic_analysis(
    task: str,
    ctx: dict[str, Any],
    *,
    llm: Any | None = None,
    model_hint: ModelHint = ModelHint.VERIFIER_REVIEW,
) -> dict[str, Any]:
    """ReviewGate logic-analysis delegate.

    Same call shape as spec_compliance but routes under the same
    VERIFIER_REVIEW hint (no per-delegate hint distinction in v1.2 — the
    policy/role resolution differentiates via ``role=`` if needed later).
    """
    if llm is None:
        return {"success": True, "result": []}

    user_msg = (
        f"{task}\n\n"
        f"Code (first 2000 chars): {str(ctx.get('code', ''))[:2000]!r}\n"
        f"File: {ctx.get('file_path')!r}"
    )
    response = await llm.complete(
        system="You are an independent code analyst. Return a JSON array of issues.",
        messages=[{"role": "user", "content": user_msg}],
        model_hint=model_hint,
    )
    return {"success": True, "result": _extract_response_text(response)}


async def _delegate_security_review(
    task: str,
    ctx: dict[str, Any],
    *,
    llm: Any | None = None,
    model_hint: ModelHint = ModelHint.VERIFIER_SECURITY,
) -> dict[str, Any]:
    """SecurityScan LLM-path delegate (forward-looking).

    The current ``SecurityScan`` is regex/AST-only; this delegate exists
    so that when an LLM-augmented path is added, it will route under
    ``VERIFIER_SECURITY`` (the deliberate cost-downgrade per v1.2).
    Today this is unused by default but wired into ``register_defaults``
    so the cost-conscious model mapping is already in place.
    """
    if llm is None:
        return {"success": True, "result": []}

    user_msg = (
        f"{task}\n\n"
        f"Code (first 2000 chars): {str(ctx.get('code', ''))[:2000]!r}\n"
        f"File: {ctx.get('file_path')!r}"
    )
    response = await llm.complete(
        system="You are an independent security analyst. Return a JSON array of findings.",
        messages=[{"role": "user", "content": user_msg}],
        model_hint=model_hint,
    )
    return {"success": True, "result": _extract_response_text(response)}


class VerificationAdapter:
    """Registry + dispatcher for verification pipelines."""

    def __init__(self, wal: "WALManager", llm: Any | None = None):
        self._wal = wal
        self._llm = llm
        self._pipelines: dict[str, VerificationPipeline] = {}

    def register(self, name: str, pipeline: VerificationPipeline) -> None:
        if name in self._pipelines:
            raise ValueError(f"Pipeline {name!r} already registered")
        self._pipelines[name] = pipeline

    def list_pipelines(self) -> list[str]:
        return list(self._pipelines.keys())

    def register_defaults(self) -> None:
        """Register the 4 default verification pipelines.

        Uses a single VerificationPipeline instance registered under 4 names
        (security, tdd, test, review). Each name maps to the same pipeline
        orchestrator which coordinates all 4 gates.

        The delegate factory wires the injected LLM (if any) to the
        ReviewGate / SecurityScan delegates with explicit ModelHints —
        VERIFIER_REVIEW for spec/logic delegates and VERIFIER_SECURITY
        for the security delegate (per v1.2 cost-downgrade decision).
        """

        async def spec_delegate(task: str, ctx: dict[str, Any]) -> dict[str, Any]:
            return await _delegate_spec_compliance(task, ctx, llm=self._llm)

        async def logic_delegate(task: str, ctx: dict[str, Any]) -> dict[str, Any]:
            return await _delegate_logic_analysis(task, ctx, llm=self._llm)

        async def security_delegate(task: str, ctx: dict[str, Any]) -> dict[str, Any]:
            return await _delegate_security_review(task, ctx, llm=self._llm)

        # The pipeline's delegate_task is a single callable; we wrap
        # spec + logic + security into one dispatcher so the v1 contract
        # (delegate_task(task, ctx) -> dict) is preserved.
        async def combined_delegate(task: str, ctx: dict[str, Any]) -> dict[str, Any]:
            review_type = ctx.get("review_type")
            if review_type == "security":
                return await security_delegate(task, ctx)
            if review_type == "logic_analysis":
                return await logic_delegate(task, ctx)
            # default: spec_compliance
            return await spec_delegate(task, ctx)

        from src.verification import VerificationPipeline

        pipeline = VerificationPipeline(delegate_task=combined_delegate)
        for name in ("security", "tdd", "test", "review"):
            self.register(name, pipeline)

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