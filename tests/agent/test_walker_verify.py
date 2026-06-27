"""Tests for PlanWalker VERIFY step execution through VerificationPipeline."""
from __future__ import annotations

import asyncio

import pytest

from src.agent.control import ControlChannel, StepResult
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind, new_plan_id, new_step_id
from src.agent.walker import PlanWalker, StepFailure


class FakePipeline:
    """A fake verification pipeline that records calls and returns a fixed result."""

    def __init__(self, result: dict | None = None) -> None:
        self._result = result or {"passed": True, "details": "ok"}
        self.calls: list[tuple[str, dict]] = []

    def run(self, code: str, context: dict) -> dict:
        self.calls.append((code, context))
        return self._result


class AsyncFakePipeline:
    """An async fake verification pipeline for testing sync/async compatibility."""

    def __init__(self, result: dict | None = None) -> None:
        self._result = result or {"passed": True, "details": "ok"}
        self.calls: list[tuple[str, dict]] = []

    async def run(self, code: str, context: dict) -> dict:
        await asyncio.sleep(0)
        self.calls.append((code, context))
        return self._result


def make_channel() -> ControlChannel:
    return ControlChannel()


def make_plan_with_verify_step(code: str, context: dict, on_failure: OnFailure = OnFailure.ASK) -> Plan:
    step = PlanStep(
        id=new_step_id(),
        kind=PlanStepKind.VERIFY,
        intent="verify code",
        args={"code": code, "context": context},
        on_failure=on_failure,
    )
    return Plan(plan_id=new_plan_id(), spec="test plan", steps=[step])


# ─── Tests ─────────────────────────────────────────────────────────────────────


class TestVerifyStepCallsPipeline:
    """VERIFY step calls the injected verification pipeline with correct args."""

    @pytest.mark.asyncio
    async def test_verify_step_calls_pipeline(self):
        # Build a Plan with 1 VERIFY step
        code = "print('hello')"
        context = {"file": "main.py", "line": 1}
        plan = make_plan_with_verify_step(code=code, context=context)

        # Create a fake pipeline
        fake = FakePipeline()

        # Pass it to PlanWalker
        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=None, verification=fake)

        # Run walk
        results = await walker.walk(plan)

        # Assert pipeline was called once with correct args
        assert len(fake.calls) == 1
        assert fake.calls[0] == (code, context)

        # Assert step result is done
        assert len(results) == 1
        assert results[0].status == "done"
        assert results[0].step_id == plan.steps[0].id

    @pytest.mark.asyncio
    async def test_verify_step_raises_step_failure_when_not_passed(self):
        """VERIFY step raises StepFailure when pipeline returns passed=False."""
        # Build a Plan with 1 VERIFY step
        code = "malicious_code()"
        context = {"file": "hack.py"}
        plan = make_plan_with_verify_step(code=code, context=context, on_failure=OnFailure.SKIP)

        # Create a fake pipeline that returns failed result
        fake = FakePipeline(result={"passed": False, "details": "security violation"})

        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=None, verification=fake)

        # Run walk — should skip (on_failure=SKIP) since verification failed
        results = await walker.walk(plan)

        # Pipeline was called (retry loop calls it MAX_RETRIES_PER_STEP+1 times before skip)
        assert len(fake.calls) == 3  # 1 initial + 2 retries

        # Step should be skipped (on_failure=SKIP)
        assert len(results) == 1
        assert results[0].status == "skipped"
        assert "security violation" in results[0].error

    @pytest.mark.asyncio
    async def test_verify_step_with_async_pipeline(self):
        """VERIFY step works with an async pipeline (sync/async compatibility)."""
        code = "x = 1"
        context = {}
        plan = make_plan_with_verify_step(code=code, context=context)

        fake = AsyncFakePipeline()

        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=None, verification=fake)

        results = await walker.walk(plan)

        assert len(fake.calls) == 1
        assert fake.calls[0] == (code, context)
        assert len(results) == 1
        assert results[0].status == "done"

    @pytest.mark.asyncio
    async def test_verify_step_raises_without_pipeline(self):
        """VERIFY step raises StepFailure when no verification pipeline is injected."""
        code = "some_code()"
        context = {}
        plan = make_plan_with_verify_step(code=code, context=context, on_failure=OnFailure.SKIP)

        channel = make_channel()
        walker = PlanWalker(channel=channel, tools=None, verification=None)

        results = await walker.walk(plan)

        # Should skip because on_failure=SKIP
        assert len(results) == 1
        assert results[0].status == "skipped"
        assert "verification pipeline" in results[0].error