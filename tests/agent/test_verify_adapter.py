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