"""PlanWalker - walks Plan.steps[] emitting events via ControlChannel."""
from __future__ import annotations

import inspect
import json
from typing import Any

from .control import CommandKind, ControlChannel, StepResult
from .events import (
    AskUser,
    Paused,
    PlanCompleted,
    Resumed,
    StepCompleted,
    StepFailed,
    StepStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from .plan import OnFailure, Plan, PlanStep, PlanStepKind
from src.agents.registry import RoleRegistry  # noqa: F401 - type hints only


MAX_RETRIES_PER_STEP = 2


class PlanAborted(Exception):
    """Raised when a step's on_failure=abort triggers, or channel is aborted."""

    pass


class StepFailure(Exception):
    """Raised internally when a step's execution fails."""

    def __init__(self, step_id: str, error: str) -> None:
        super().__init__(f"step {step_id} failed: {error}")
        self.step_id = step_id
        self.error = error


class PlanWalker:
    """Walks a Plan's steps in order, emitting WalkEvents via ControlChannel.

    For each step: checks abort → awaits pause → emits StepStarted →
    calls execute_step → emits StepCompleted/StepFailed.
    """

    def __init__(
        self,
        *,
        plan: Plan,
        channel: ControlChannel,
        tools: Any,
        verification: Any = None,
        llm: Any = None,
        wal: Any = None,
        role_registry: RoleRegistry | None = None,
    ) -> None:
        self.plan = plan
        self._channel = channel
        self._tools = tools
        self._verification = verification
        self._llm = llm
        self._wal = wal
        self.role_registry = role_registry

    async def walk(self, plan: Plan) -> list[StepResult]:
        """Iterate plan.steps[], executing each and emitting events."""
        results: list[StepResult] = []
        # Determine which steps are already checkpointed (for crash recovery)
        completed_ids: set[str] = set()
        if self._wal is not None:
            completed_ids = self._wal.get_completed_step_ids(plan.plan_id)
        for idx, step in enumerate(plan.steps):
            # Pause check at step boundary (section 6.3)
            if self._channel.is_paused:
                next_step_id = plan.steps[idx].id if idx < len(plan.steps) else None
                await self._channel.emit(Paused(step_id=next_step_id))
                await self._channel.wait_if_paused()
                await self._channel.emit(Resumed())
            if self._channel.is_aborted:
                raise PlanAborted(self._channel.aborted_reason)

            # Skip already-checkpointed steps (crash recovery)
            if step.id in completed_ids:
                continue

            await self._channel.emit(StepStarted(step=step, index=idx, total=len(plan.steps)))

            try:
                result = await self.execute_step(step)
                results.append(result)
                await self._channel.emit(StepCompleted(step=step, result=result))
                # Checkpoint after each successful step
                if self._wal is not None:
                    await self._wal.checkpoint(
                        plan=plan,
                        cursor=step.id,
                        result={
                            "output": result.output if hasattr(result, "output") else None,
                            "status": result.status,
                        },
                    )
            except PlanAborted:
                raise

        await self._channel.emit(PlanCompleted(results=results))
        return results

    async def execute_step(self, step: PlanStep) -> StepResult:
        """Execute a step with retry loop (section 6.2)."""
        last_error: Exception | None = None
        for attempt in range(1 + MAX_RETRIES_PER_STEP):
            try:
                return await self._execute_step_once(step)
            except StepFailure as e:
                last_error = e
                if attempt < MAX_RETRIES_PER_STEP:
                    continue  # retry
                # All retries exhausted — hand off to failure strategy
                return await self._handle_step_failure(step, e)
            except Exception as e:
                last_error = e
                continue

        # All retries exhausted — hand off to failure strategy
        assert last_error is not None
        if isinstance(last_error, StepFailure):
            return await self._handle_step_failure(step, last_error)
        return await self._handle_step_failure(step, StepFailure(step.id, str(last_error)))

    async def _execute_step_once(self, step: PlanStep) -> StepResult:
        """Dispatch to the appropriate step-type handler."""
        if step.kind == PlanStepKind.SUBPLAN:
            return await self._execute_subplan(step)
        if step.kind == PlanStepKind.TOOL:
            return await self._execute_tool_step(step)
        elif step.kind == PlanStepKind.VERIFY:
            return await self._execute_verify_step(step)
        elif step.kind == PlanStepKind.CRITIQUE:
            return await self._execute_critique_step(step)
        elif step.kind == PlanStepKind.ASK_USER:
            return await self._execute_ask_user_step(step)
        else:
            raise StepFailure(step.id, f"unknown step kind: {step.kind}")

    async def _execute_tool_step(self, step: PlanStep) -> StepResult:
        """Execute a TOOL step: emit ToolCallStarted → tool.execute → ToolCallCompleted."""
        if not step.tool:
            raise StepFailure(step.id, "TOOL step missing tool name")

        await self._channel.emit(ToolCallStarted(tool=step.tool, args=step.args, step_id=step.id))
        try:
            output = await self._tools.execute(step.tool, step.args)
        except Exception as e:
            raise StepFailure(step.id, f"tool {step.tool} failed: {e}")
        await self._channel.emit(ToolCallCompleted(result=output, step_id=step.id))
        return StepResult(step_id=step.id, status="done", output=output)

    async def _execute_verify_step(self, step: PlanStep) -> StepResult:
        """Execute a VERIFY step using the injected verification pipeline."""
        if self._verification is None:
            raise StepFailure(step.id, "VERIFY step requires verification pipeline")
        code = step.args.get("code", "")
        context = step.args.get("context", {})
        result = self._verification.run(code=code, context=context)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict) and not result.get("passed", False):
            raise StepFailure(step.id, f"verification failed: {result.get('details', result)}")
        return StepResult(step_id=step.id, status="done", output=result)

    async def _execute_critique_step(self, step: PlanStep) -> StepResult:
        """Execute a CRITIQUE step: prompt LLM to self-review the step outcome."""
        if self._llm is None:
            raise StepFailure(step.id, "CRITIQUE step requires LLM client")
        context = step.args.get("context", "")
        user_msg = (
            f"Step intent: {step.intent}\n"
            f"Step context: {context}\n"
            f"Success criteria: {step.success_criteria}\n"
            f'Does this step pass? Respond with JSON: {{"passes": bool, "feedback": "..."}}'
        )
        response = await self._llm.complete(
            system="You critique step outcomes.",
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, IndexError, AttributeError) as e:
            raise StepFailure(step.id, f"CRITIQUE: invalid LLM response: {e}")
        if not data.get("passes", False):
            raise StepFailure(step.id, f"CRITIQUE failed: {data.get('feedback', data)}")
        return StepResult(step_id=step.id, status="done", output=data)

    async def _execute_ask_user_step(self, step: PlanStep) -> StepResult:
        """Execute an ASK_USER step: emit AskUser, block for ANSWER_QUESTION command."""
        question = step.args.get("question", "")
        options = step.args.get("options", [])
        await self._channel.emit(AskUser(step=step, question=question, options=options))

        # Block until ANSWER_QUESTION command for this step
        while True:
            cmd = await self._channel.recv_command()
            if cmd.kind == CommandKind.ANSWER_QUESTION and cmd.payload.get("step_id") == step.id:
                return StepResult(
                    step_id=step.id,
                    status="done",
                    output=cmd.payload.get("answer"),
                )
            # Re-queue other commands (unlikely in practice but safe)
            await self._channel.send_command(cmd)

    async def _handle_step_failure(self, step: PlanStep, error: StepFailure) -> StepResult:
        """Route on_failure strategy (section 6.2)."""
        if step.on_failure == OnFailure.ABORT:
            await self._channel.emit(StepFailed(step=step, error=str(error)))
            raise PlanAborted(f"step {step.id} aborted: {error}")
        elif step.on_failure == OnFailure.RETRY:
            return await self.execute_step(step)
        elif step.on_failure == OnFailure.SKIP:
            await self._channel.emit(StepFailed(step=step, error=str(error)))
            return StepResult(step_id=step.id, status="skipped", error=str(error))
        elif step.on_failure == OnFailure.ASK:
            await self._channel.emit(
                AskUser(
                    step=step,
                    question=f"Step {step.id} failed: {error}. Continue?",
                    options=["retry", "skip", "abort"],
                )
            )
            while True:
                cmd = await self._channel.recv_command()
                if cmd.kind == CommandKind.ANSWER_QUESTION and cmd.payload.get("step_id") == step.id:
                    answer = cmd.payload.get("answer", "")
                    if answer == "retry":
                        return await self.execute_step(step)
                    elif answer == "skip":
                        await self._channel.emit(StepFailed(step=step, error=str(error)))
                        return StepResult(step_id=step.id, status="skipped", error=str(error))
                    elif answer == "abort":
                        await self._channel.emit(StepFailed(step=step, error=str(error)))
                        raise PlanAborted(f"step {step.id} aborted by user")
        # Default fallback
        return StepResult(step_id=step.id, status="skipped", error=str(error))

    async def _execute_subplan(self, step: PlanStep) -> StepResult:
        """Execute a SUBPLAN step by spawning a child Plan via the role registry.

        Args:
            step: The PlanStep with kind=SUBPLAN, role set, tool=task description.

        Returns:
            StepResult with status="completed" if sub-plan succeeded,
            status="failed" if sub-plan aborted (caught here so the parent's
            on_failure strategy applies).

        Raises:
            RuntimeError: If role_registry was not configured on this walker.
        """
        if self.role_registry is None:
            raise RuntimeError("RoleRegistry not configured on PlanWalker")
        if step.role is None:
            raise ValueError(f"SUBPLAN step {step.id} has no role")

        sub_plan: Plan | None = None
        try:
            sub_plan = self.role_registry.spawn(
                role=step.role,
                task=step.tool,
                context=step.subplan_args or {},
            )
            sub_result = await self._runtime_for_subplan().walk(sub_plan)
            return StepResult(
                step_id=step.id,
                status=sub_result.status,
                metadata={
                    "subplan_id": sub_plan.plan_id,
                    "subplan_result": {"status": sub_result.status},
                    "subplan_aborted": False,
                },
            )
        except PlanAborted as e:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=str(e),
                metadata={
                    "subplan_id": sub_plan.plan_id if sub_plan else "unknown",
                    "subplan_aborted": True,
                },
            )

    def _runtime_for_subplan(self) -> "AgentRuntime":
        """Return the runtime handle for sub-plan walks.

        The runtime reference is set by AgentRuntime before invoking walk().
        """
        if not hasattr(self, "_runtime") or self._runtime is None:
            raise RuntimeError(
                "PlanWalker._runtime is not set; AgentRuntime must call "
                "walker._runtime = self before walking sub-plans."
            )
        return self._runtime

