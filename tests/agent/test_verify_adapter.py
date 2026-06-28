import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.control import StepResult
from src.agent.plan import PlanStep, PlanStepKind
from src.agent.verify_adapter import VerificationAdapter


class FakePipeline:
    def __init__(self, outcome):
        self._outcome = outcome

    async def verify(self, step, step_result, ctx):
        return self._outcome


@pytest.mark.asyncio
async def test_register_and_list_pipelines():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register("security", FakePipeline(None))
    adapter.register("test", FakePipeline(None))
    assert "security" in adapter.list_pipelines()
    assert "test" in adapter.list_pipelines()


@pytest.mark.asyncio
async def test_run_invokes_pipeline_with_step_and_context():
    wal = MagicMock()
    wal.context_for_step = MagicMock(return_value={"files_touched": ["src/auth/login.py"]})
    adapter = VerificationAdapter(wal=wal)
    outcome = MagicMock(passed=True, errors=[], warnings=[])
    adapter.register("security", FakePipeline(outcome))
    step = PlanStep(id="step-1", kind=PlanStepKind.VERIFY, intent="Verify security", pipeline="security")
    result = await adapter.run(step, StepResult(step_id="step-1", status="completed"), ctx=MagicMock())
    assert result.passed is True


@pytest.mark.asyncio
async def test_run_raises_for_unregistered_pipeline():
    adapter = VerificationAdapter(wal=MagicMock())
    step = PlanStep(id="step-1", kind=PlanStepKind.VERIFY, intent="Verify", pipeline="nonexistent")
    with pytest.raises(KeyError):
        await adapter.run(step, StepResult(step_id="step-1", status="completed"), ctx=MagicMock())


def test_register_defaults_includes_four_pipelines():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register_defaults()
    names = adapter.list_pipelines()
    assert "security" in names
    assert "tdd" in names
    assert "test" in names
    assert "review" in names


def test_register_replaces_existing_pipeline():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register("security", FakePipeline(None))
    with pytest.raises(ValueError, match="already registered"):
        adapter.register("security", FakePipeline("replaced"))


def test_list_pipelines_returns_insertion_order():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register("zebra", FakePipeline(None))
    adapter.register("apple", FakePipeline(None))
    adapter.register("beta", FakePipeline(None))
    names = adapter.list_pipelines()
    assert names == ["zebra", "apple", "beta"]


def test_register_defaults_can_be_called_multiple_times():
    adapter = VerificationAdapter(wal=MagicMock())
    adapter.register_defaults()
    with pytest.raises(ValueError, match="already registered"):
        adapter.register_defaults()


@pytest.mark.asyncio
async def test_run_with_no_pipelines_registered_raises_keyerror():
    adapter = VerificationAdapter(wal=MagicMock())
    step = PlanStep(id="step-1", kind=PlanStepKind.VERIFY, intent="Verify", pipeline="security")
    with pytest.raises(KeyError):
        await adapter.run(step, StepResult(step_id="step-1", status="completed"), ctx=MagicMock())


@pytest.mark.asyncio
async def test_run_passes_ctx_to_pipeline():
    wal = MagicMock()
    ctx = {"foo": "bar"}
    wal.context_for_step = MagicMock(return_value={})
    received_ctx = None
    class CapturingPipeline:
        async def verify(self, step, step_result, ctx):
            nonlocal received_ctx
            received_ctx = ctx
            return MagicMock(passed=True, errors=[], warnings=[])
    adapter = VerificationAdapter(wal=wal)
    adapter.register("capture", CapturingPipeline())
    step = PlanStep(id="s1", kind=PlanStepKind.VERIFY, intent="x", pipeline="capture")
    await adapter.run(step, StepResult(step_id="s1", status="completed"), ctx=ctx)
    assert received_ctx is not None
    assert received_ctx.get("foo") == "bar"


@pytest.mark.asyncio
async def test_run_with_failed_outcome():
    wal = MagicMock()
    wal.context_for_step = MagicMock(return_value={})
    adapter = VerificationAdapter(wal=wal)
    failed_outcome = MagicMock(passed=False, errors=["assertion failed"], warnings=[])
    adapter.register("test", FakePipeline(failed_outcome))
    step = PlanStep(id="step-1", kind=PlanStepKind.VERIFY, intent="Run tests", pipeline="test")
    result = await adapter.run(step, StepResult(step_id="step-1", status="completed"), ctx={})
    assert result.passed is False
    assert "assertion failed" in result.errors


@pytest.mark.asyncio
async def test_run_with_wal_context_merged_in():
    wal = MagicMock()
    wal.context_for_step = MagicMock(return_value={"files_touched": ["src/main.py"]})
    adapter = VerificationAdapter(wal=wal)
    received_ctx = {}
    class CapturingPipeline:
        async def verify(self, step, step_result, ctx):
            nonlocal received_ctx
            received_ctx = ctx
            return MagicMock(passed=True, errors=[], warnings=[])
    adapter.register("capture", CapturingPipeline())
    step = PlanStep(id="s1", kind=PlanStepKind.VERIFY, intent="x", pipeline="capture")
    await adapter.run(step, StepResult(step_id="s1", status="completed"), ctx={})
    assert received_ctx.get("wal", {}).get("files_touched") == ["src/main.py"]