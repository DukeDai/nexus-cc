"""Tests for Planner - LLM to structured Plan with JSON retry and markdown stripping."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.plan import OnFailure, Plan, PlanStepKind


class FakeLLM:
    """Fake LLM that returns predefined responses for testing."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_count = 0

    async def complete(self, *, system: str, messages: list[dict]) -> MagicMock:
        """Return the next response in the list."""
        idx = min(self._call_count, len(self._responses) - 1)
        text = self._responses[idx]
        self._call_count += 1
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=text)]
        return mock_response

    @property
    def call_count(self) -> int:
        return self._call_count


class TestParseValidJsonToPlan:
    """Test that Planner.plan() correctly parses valid LLM JSON response."""

    @pytest.mark.asyncio
    async def test_parse_valid_json_to_plan(self):
        """Planner.plan() returns Plan with correct steps from valid JSON."""
        json_response = """{
  "spec": "Build a CLI tool",
  "assumptions": ["User has Python installed"],
  "risks": ["Package may not be available"],
  "steps": [
    {
      "id": "step_abc12345",
      "kind": "TOOL",
      "intent": "Install the package",
      "tool": "pip",
      "args": {"package": "mycli"},
      "success_criteria": "Package installed successfully",
      "on_failure": "retry",
      "timeout_s": 60
    },
    {
      "id": "step_def67890",
      "kind": "VERIFY",
      "intent": "Verify installation",
      "tool": "bash",
      "args": {"cmd": "mycli --version"},
      "success_criteria": "Version displayed",
      "on_failure": "abort",
      "timeout_s": 30
    }
  ]
}"""
        fake_llm = FakeLLM([json_response])
        from agent.planner import Planner

        planner = Planner(llm=fake_llm)
        plan = await planner.plan("Build a CLI tool")

        assert plan.spec == "Build a CLI tool"
        assert plan.assumptions == ["User has Python installed"]
        assert plan.risks == ["Package may not be available"]
        assert len(plan.steps) == 2
        assert plan.steps[0].id == "step_abc12345"
        assert plan.steps[0].kind == PlanStepKind.TOOL
        assert plan.steps[0].intent == "Install the package"
        assert plan.steps[0].tool == "pip"
        assert plan.steps[0].args == {"package": "mycli"}
        assert plan.steps[0].success_criteria == "Package installed successfully"
        assert plan.steps[0].on_failure == OnFailure.RETRY
        assert plan.steps[0].timeout_s == 60
        assert plan.steps[1].kind == PlanStepKind.VERIFY
        assert plan.steps[1].on_failure == OnFailure.ABORT


class TestRetryOnInvalidJson:
    """Test that Planner retries on invalid JSON and succeeds on valid JSON."""

    @pytest.mark.asyncio
    async def test_retry_on_invalid_json(self):
        """Planner.plan() retries 3 times and succeeds when valid JSON on 3rd attempt."""
        invalid_json = "This is not valid JSON"
        valid_json = """{
  "spec": "Simple task",
  "assumptions": [],
  "risks": [],
  "steps": [
    {
      "id": "step_11111111",
      "kind": "TOOL",
      "intent": "Do the thing",
      "tool": "bash",
      "args": {"cmd": "echo hello"},
      "success_criteria": "Hello printed",
      "on_failure": "ask",
      "timeout_s": 30
    }
  ]
}"""
        fake_llm = FakeLLM([invalid_json, invalid_json, valid_json])

        from agent.planner import Planner

        planner = Planner(llm=fake_llm)
        plan = await planner.plan("Simple task")

        assert plan.spec == "Simple task"
        assert len(plan.steps) == 1
        assert fake_llm.call_count == 3


class TestStripMarkdownCodeBlocks:
    """Test that Planner strips markdown code fences from JSON responses."""

    @pytest.mark.asyncio
    async def test_strip_markdown_code_blocks(self):
        """Planner.plan() successfully parses JSON wrapped in ```json ... ```."""
        markdown_json = """```json
{
  "spec": "Markdown test",
  "assumptions": [],
  "risks": [],
  "steps": [
    {
      "id": "step_22222222",
      "kind": "CRITIQUE",
      "intent": "Review the code",
      "tool": null,
      "args": {},
      "success_criteria": "Code reviewed",
      "on_failure": "skip",
      "timeout_s": 45
    }
  ]
}
```"""
        fake_llm = FakeLLM([markdown_json])

        from agent.planner import Planner

        planner = Planner(llm=fake_llm)
        plan = await planner.plan("Review the code")

        assert plan.spec == "Markdown test"
        assert len(plan.steps) == 1
        assert plan.steps[0].kind == PlanStepKind.CRITIQUE
        assert plan.steps[0].on_failure == OnFailure.SKIP