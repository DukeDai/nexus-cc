"""Tests for Planner arg-schema self-correction loop.

These tests pin the v1.2 contract: when the Planner is constructed with a
ToolRegistry, it validates each TOOL step's args against the matching tool's
args_schema. On mismatch the planner re-prompts the LLM with a focused
error message; after N=2 retries it falls back to pass-through with a
WARNING log (preserving v1.1 behavior). When the Planner is constructed
without a ToolRegistry (the legacy kwarg-free signature), behavior is
unchanged — no validation, no re-prompt.

We mock the LLM to return scripts of responses so we can deterministically
exercise each branch (no retries, exactly 1 retry, all retries failed).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.plan import Plan, PlanStepKind
from src.agent.planner import (
    ArgSchemaValidationError,
    Planner,
    _validate_args_against_schema,
    _validate_plan_tool_args,
)
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """Fake LLM that returns predefined responses in order.

    Each response is returned once; on the nth+1 call the last response is
    re-used so we always satisfy the test's expectations.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        # Record (messages, kwargs) for each call so tests can inspect what
        # the planner actually sent (especially re-prompts).
        self.calls: list[dict] = []

    async def complete(self, *, system: str, messages: list[dict], **kwargs) -> MagicMock:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        text = self._responses[idx]
        # Snapshot the call; the planner forwards ``model_hint``/``model``
        # as kwargs but the *content* that matters is the user message text,
        # which we join for easy substring assertions.
        user_text = " ".join(str(m.get("content", "")) for m in messages)
        self.calls.append({"system": system, "user_text": user_text, "kwargs": dict(kwargs)})
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=text)]
        return mock_response

    @property
    def call_count(self) -> int:
        return self._call_count


def _valid_plan_json(*, tool: str = "Echo", args: dict | None = None) -> str:
    args = args if args is not None else {"message": "hi"}
    return (
        '{"spec":"t","assumptions":[],"risks":[],'
        '"steps":[{"id":"step_aaaaaaaa","kind":"TOOL","intent":"say hi",'
        f'"tool":"{tool}","args":{json_dumps(args)},'
        '"success_criteria":"ok","on_failure":"ask","timeout_s":30}]}'
    )


def json_dumps(obj: object) -> str:
    import json

    return json.dumps(obj)


class EchoTool:
    """Minimal tool with a strict args_schema for tests."""

    name = "Echo"
    description = "Echo a message."
    args_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "times": {"type": "integer", "default": 1},
        },
        "required": ["message"],
    }

    async def execute(self, *, message: str, times: int = 1) -> dict[str, object]:
        return {"echoed": message * times}


def _registry_with_echo() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


# ---------------------------------------------------------------------------
# Pure validator tests (no LLM, no Planner)
# ---------------------------------------------------------------------------


class TestValidateArgsAgainstSchema:
    """Sanity-check the lightweight JSON-Schema validator in isolation."""

    def test_accepts_valid_args(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
        assert _validate_args_against_schema({"x": "ok"}, schema) == []

    def test_rejects_missing_required(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
        errors = _validate_args_against_schema({}, schema)
        assert any("missing" in e for e in errors)

    def test_rejects_wrong_type(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        errors = _validate_args_against_schema({"x": 123}, schema)
        assert any("expected string" in e for e in errors)

    def test_allows_optional_fields_missing(self):
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "string"},
                "n": {"type": "integer", "default": 1},
            },
            "required": ["x"],
        }
        assert _validate_args_against_schema({"x": "ok"}, schema) == []

    def test_rejects_non_object_args(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
        errors = _validate_args_against_schema("not an object", schema)
        assert errors  # should fail

    def test_handles_boolean_not_treated_as_integer(self):
        # Per JSON Schema, integer and boolean are disjoint.
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        }
        errors = _validate_args_against_schema({"n": True}, schema)
        assert errors


class TestValidatePlanToolArgs:
    def test_unknown_tool_is_skipped(self):
        reg = _registry_with_echo()
        plan_text = _valid_plan_json(tool="NotATool", args={"message": "hi"})
        plan = _parse_text(plan_text)
        errors = _validate_plan_tool_args(plan, reg)
        assert errors == []

    def test_valid_args_produce_no_errors(self):
        reg = _registry_with_echo()
        plan = _parse_text(_valid_plan_json(args={"message": "hi"}))
        errors = _validate_plan_tool_args(plan, reg)
        assert errors == []

    def test_missing_required_field_is_reported(self):
        reg = _registry_with_echo()
        plan = _parse_text(_valid_plan_json(args={"times": 3}))  # missing "message"
        errors = _validate_plan_tool_args(plan, reg)
        assert len(errors) == 1
        assert isinstance(errors[0], ArgSchemaValidationError)
        assert errors[0].tool == "Echo"
        assert any("message" in e for e in errors[0].errors)

    def test_wrong_type_is_reported(self):
        reg = _registry_with_echo()
        plan = _parse_text(_valid_plan_json(args={"message": 123}))
        errors = _validate_plan_tool_args(plan, reg)
        assert len(errors) == 1
        assert any("expected string" in e for e in errors[0].errors)


def _parse_text(text: str) -> Plan:
    """Helper: parse a Plan JSON string using the planner's internal helper."""
    import json as _json

    from src.agent.planner import _parse_plan_json

    text = text.strip()
    if text.startswith("```"):
        import re
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        text = m.group(1) if m else text
    return _parse_plan_json(text)


# ---------------------------------------------------------------------------
# Planner self-correction loop tests (using a ToolRegistry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_no_retry_when_args_valid():
    """Mocked LLM emits valid args on the first try — no re-prompt, no raise."""
    reg = _registry_with_echo()
    llm = ScriptedLLM([_valid_plan_json(args={"message": "hi"})])
    planner = Planner(llm=llm, tool_registry=reg)

    plan = await planner.plan("say hi")

    assert len(plan.steps) == 1
    assert plan.steps[0].tool == "Echo"
    assert plan.steps[0].args == {"message": "hi"}
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_planner_retries_once_on_invalid_args_then_succeeds():
    """First LLM emit has invalid args; second emit fixes them. 1 retry total."""
    reg = _registry_with_echo()
    invalid = _valid_plan_json(args={"times": 3})  # missing required "message"
    valid = _valid_plan_json(args={"message": "hello"})
    llm = ScriptedLLM([invalid, valid])
    planner = Planner(llm=llm, tool_registry=reg, max_arg_schema_retries=2)

    plan = await planner.plan("say hi")

    assert llm.call_count == 2
    assert plan.steps[0].args == {"message": "hello"}

    # Sanity: the re-prompt included the validation error so the LLM can
    # see which field it got wrong (and WHICH tool — case insensitive).
    last_user_text = llm.calls[-1]["user_text"]
    assert "Echo" in last_user_text or "echo" in last_user_text.lower()
    assert "message" in last_user_text


@pytest.mark.asyncio
async def test_planner_passes_through_with_warning_when_args_remain_invalid_after_max_retries(caplog):
    """LLM keeps emitting invalid args; after MAX retries the planner falls back
    to v1.1 pass-through behavior instead of raising. A warning is logged.
    """
    import logging
    reg = _registry_with_echo()
    invalid_1 = _valid_plan_json(args={"times": 3})
    invalid_2 = _valid_plan_json(args={"times": 3})
    invalid_3 = _valid_plan_json(args={"times": 3})
    llm = ScriptedLLM([invalid_1, invalid_2, invalid_3])
    planner = Planner(llm=llm, tool_registry=reg, max_arg_schema_retries=2, max_retries=1)

    with caplog.at_level(logging.WARNING, logger="src.agent.planner"):
        plan = await planner.plan("say hi")

    # Plan returned with the invalid args (pass-through), not raised.
    assert plan.steps[0].tool == "Echo"
    assert plan.steps[0].args == {"times": 3}
    # 1 initial attempt + 2 self-correction retries = 3 total LLM calls.
    assert llm.call_count == 3
    # Warning was emitted with the offending errors so they're observable.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("self-correction exhausted" in r.getMessage() for r in warning_records)
    assert any("message" in r.getMessage() for r in warning_records)


@pytest.mark.asyncio
async def test_planner_zero_arg_retries_means_no_validation():
    """Setting max_arg_schema_retries=0 disables validation entirely (v1.1 fallback)."""
    reg = _registry_with_echo()
    # Emit args that would normally fail validation; should not raise.
    invalid = _valid_plan_json(args={"times": 3})  # missing "message"
    llm = ScriptedLLM([invalid])
    planner = Planner(llm=llm, tool_registry=reg, max_arg_schema_retries=0)

    plan = await planner.plan("say hi")

    assert llm.call_count == 1
    assert plan.steps[0].args == {"times": 3}


@pytest.mark.asyncio
async def test_planner_without_registry_skips_validation():
    """Legacy Planner signature (no tool_registry) bypasses validation entirely."""
    invalid = _valid_plan_json(args={"times": 3})  # missing "message"
    llm = ScriptedLLM([invalid])
    # NOTE: no tool_registry kwarg — falls back to v1.1 behavior.
    planner = Planner(llm=llm)

    plan = await planner.plan("say hi")

    assert llm.call_count == 1
    assert plan.steps[0].tool == "Echo"
    assert plan.steps[0].args == {"times": 3}


@pytest.mark.asyncio
async def test_planner_self_correct_only_runs_for_tool_steps():
    """Non-TOOL steps (VERIFY, CRITIQUE, etc.) never trigger re-prompt."""
    import json

    reg = _registry_with_echo()
    # A pure VERIFY plan — no TOOL steps, so validation is a no-op even if a
    # tool were referenced implicitly via success_criteria text.
    verify_plan = (
        '{"spec":"v","assumptions":[],"risks":[],'
        '"steps":[{"id":"step_aaaaaaaa","kind":"VERIFY","intent":"check",'
        '"tool":null,"args":{"code":"1==1"},"success_criteria":"pass",'
        '"on_failure":"ask","timeout_s":30}]}'
    )
    llm = ScriptedLLM([verify_plan])
    planner = Planner(llm=llm, tool_registry=reg)

    plan = await planner.plan("verify something")

    assert llm.call_count == 1
    assert plan.steps[0].kind == PlanStepKind.VERIFY
