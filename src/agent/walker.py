"""PlanWalker - walks Plan.steps[] emitting events via ControlChannel."""
from __future__ import annotations

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
        channel: ControlChannel,
        tools: Any,
        verification: Any = None,
        llm: Any = None,
        wal: Any = None,
    ) -> None:
        self._channel = channel
        self._tools = tools
        self._verification = verification
        self._llm = llm
        self._wal = wal

    async def walk(self, plan: Plan) -> list[StepResult]:
        """Iterate plan.steps[], executing each and emitting events."""
        results: list[StepResult] = []
        for idx, step in enumerate(plan.steps):
            # Pause check at step boundary (section 6.3)
            if self._channel.is_paused:
                next_step_id = plan.steps[idx].id if idx < len(plan.steps) else None
                await self._channel.emit(Paused(step_id=next_step_id))
                await self._channel.wait_if_paused()
                await self._channel.emit(Resumed())
            if self._channel.is_aborted:
                raise PlanAborted(self._channel.aborted_reason)

            await self._channel.emit(StepStarted(step=step, index=idx, total=len(plan.steps)))

            try:
                result = await self.execute_step(step)
                results.append(result)
                await self._channel.emit(StepCompleted(step=step, result=result))
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
        """Placeholder for Task 8 (VERIFY step)."""
        return StepResult(step_id=step.id, status="done", output=None)

    async def _execute_critique_step(self, step: PlanStep) -> StepResult:
        """Placeholder for Task 9 (CRITIQUE step)."""
        return StepResult(step_id=step.id, status="done", output=None)

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
