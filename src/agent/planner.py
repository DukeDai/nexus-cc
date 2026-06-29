"""Planner - LLM to structured Plan with JSON retry + arg-schema validation."""
from __future__ import annotations

import json
import re
from typing import Any

from src.llm.model_policy import ModelHint

from .plan import Plan, PlanStep, PlanStepKind, OnFailure, new_plan_id, new_step_id


SYSTEM_PROMPT = """You are a planning agent. Given a user task, produce a structured execution plan.

Output ONLY a JSON object with this schema:
{
  "spec": "<one-sentence summary>",
  "assumptions": ["..."],
  "risks": ["..."],
  "steps": [
    {
      "id": "step_<8 hex chars>",
      "kind": "TOOL" | "VERIFY" | "CRITIQUE" | "ASK_USER",
      "intent": "...",
      "tool": "<tool name>" | null,
      "args": {...},
      "success_criteria": "...",
      "on_failure": "abort" | "retry" | "skip" | "ask",
      "timeout_s": <int>
    }
  ]
}

Available tool names (use exactly one of these — never invent names):
- Read (read files)
- Write (create/overwrite files)
- Edit (in-place file edits)
- Bash (run shell commands, e.g. pytest)
- Glob (find files by pattern)
- Grep (search file contents)
- Git (git operations)
- WebSearch (web search)

Constraints:
- 2-10 steps total
- Prefer TOOL steps; VERIFY for test/lint gates; ASK_USER only when truly ambiguous
- Each step must have concrete success_criteria
- For TOOL steps, "tool" must be one of the names above, with case preserved
- Tool args must match the tool's schema (required fields, correct types)
"""


# Max times to retry the LLM when step args fail schema validation. After this
# many self-correction attempts the planner raises (caller may fall back to
# ask_user / v1.1 path).
MAX_ARG_SCHEMA_RETRIES = 2


class ArgSchemaValidationError(Exception):
    """Raised when a TOOL step's args do not match the tool's args_schema.

    Attributes:
        step_id: ID of the offending step.
        tool: Tool name as emitted by the LLM (case may vary).
        errors: Human-readable list of validation errors.
    """

    def __init__(self, step_id: str, tool: str, errors: list[str]) -> None:
        self.step_id = step_id
        self.tool = tool
        self.errors = errors
        super().__init__(
            f"step {step_id}: tool {tool!r} args failed schema validation: " + "; ".join(errors)
        )


def _strip_markdown(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


def _validate_args_against_schema(args: Any, schema: dict[str, Any]) -> list[str]:
    """Lightweight validator for the JSON-Schema subset our tools use.

    Supports:
      - top-level type=object with ``properties`` and ``required``
      - per-property ``type`` (string, integer, number, boolean, array, object, null)
      - ``default`` is informational only; missing optional fields are accepted

    Returns a list of human-readable error messages (empty == valid).
    """
    errors: list[str] = []
    if schema.get("type", "object") != "object":
        return [f"unsupported top-level schema type: {schema.get('type')!r}"]
    if not isinstance(args, dict):
        return [f"expected object, got {type(args).__name__}"]

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = list(schema.get("required", []))

    for req in required:
        if req not in args:
            errors.append(f"missing required field {req!r}")
    for key, value in args.items():
        if key not in properties:
            # Allow unknown keys but record for the LLM's awareness; tolerate
            # over-specification rather than failing.
            continue
        prop = properties[key]
        expected = prop.get("type")
        if expected is None:
            continue
        if not _value_matches_type(value, expected):
            errors.append(
                f"field {key!r}: expected {expected}, got {_python_type_name(value)}"
            )
    return errors


def _value_matches_type(value: Any, expected: str) -> bool:
    """Match a Python value against a JSON-Schema type tag."""
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        # JSON: integers only, not floats.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    # Unknown type — be permissive.
    return True


def _python_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _parse_plan_json(text: str) -> Plan:
    data = json.loads(text)
    steps = []
    for s in data.get("steps", []):
        # Accept both "TOOL" (enum member name) and "tool" (enum value).
        # SYSTEM_PROMPT instructs the LLM to emit the former; real models
        # sometimes emit lowercase. Normalize to the enum value.
        raw_kind = str(s["kind"]).strip()
        try:
            kind = PlanStepKind(raw_kind)
        except ValueError:
            kind = PlanStepKind(raw_kind.lower())
        raw_on_failure = s.get("on_failure", "ask")
        try:
            on_failure = OnFailure(raw_on_failure)
        except ValueError:
            on_failure = OnFailure(str(raw_on_failure).lower())
        steps.append(PlanStep(
            id=s.get("id") or new_step_id(),
            kind=kind,
            intent=s["intent"],
            tool=s.get("tool"),
            args=s.get("args", {}),
            success_criteria=s.get("success_criteria", ""),
            on_failure=on_failure,
            timeout_s=int(s.get("timeout_s", 120)),
        ))
    return Plan(
        plan_id=new_plan_id(),
        spec=data["spec"],
        steps=steps,
        assumptions=data.get("assumptions", []),
        risks=data.get("risks", []),
    )


def _validate_plan_tool_args(plan: Plan, tool_registry: Any) -> list[ArgSchemaValidationError]:
    """Return one validation error per TOOL step whose args fail to validate.

    Silently skips TOOL steps that reference an unknown tool (the planner will
    surface unknown-tool errors elsewhere via ToolRegistry.get).
    """
    errs: list[ArgSchemaValidationError] = []
    for step in plan.steps:
        if step.kind != PlanStepKind.TOOL or not step.tool:
            continue
        try:
            tool = tool_registry.get(step.tool)
        except KeyError:
            # Unknown tool is a different class of error; let that surface
            # elsewhere (or via LLM self-correction with a separate message).
            continue
        schema = getattr(tool, "args_schema", None)
        if not schema:
            continue
        step_errors = _validate_args_against_schema(step.args, schema)
        if step_errors:
            errs.append(ArgSchemaValidationError(step.id, tool.name, step_errors))
    return errs


class Planner:
    def __init__(
        self,
        *,
        llm: Any,
        max_retries: int = 3,
        tool_registry: Any = None,
        max_arg_schema_retries: int = MAX_ARG_SCHEMA_RETRIES,
    ) -> None:
        self._llm = llm
        self._max_retries = max_retries
        # Optional: when provided, the planner validates TOOL-step args against
        # the registry's args_schema and re-prompts on mismatch. When ``None``
        # (the default), planner behavior is unchanged from v1.1 — useful for
        # callers that construct a Planner without a ToolRegistry and for
        # tests that want to assert on the raw LLM output.
        self._tool_registry = tool_registry
        self._max_arg_schema_retries = max(0, max_arg_schema_retries)

    async def plan(
        self,
        task: str,
        *,
        spec: str | None = None,
        memory_context: str = "",
        model_hint: ModelHint = ModelHint.PLANNER,
        model_name: str | None = None,
    ) -> Plan:
        """Generate a structured Plan from a task description.

        Args:
            task: Natural-language task description.
            spec: Optional additional spec content.
            memory_context: Past-similar-tasks context to prepend to the system prompt.
            model_hint: Hint consumed by the v1.2 ModelRouter (when the feature
                flag ``NEXUS_USE_MODEL_ROUTER=1`` is set, the underlying
                ``_RouterAdapter`` will route this call to the model resolved
                for ``model_hint``). Defaults to ``ModelHint.PLANNER``. CRITIQUE
                sub-plans should pass ``ModelHint.CRITIQUE``. When the flag is
                unset, the legacy ``LLMClient`` is used and this argument has no
                effect — behavior is unchanged.
            model_name: Optional explicit model name (e.g.
                ``"claude-haiku-4-5"``). When provided, this is forwarded to
                the underlying LLM client as the ``model`` payload field,
                overriding the client's default. Per-role overrides in
                ``.nexus/policy.yaml`` (resolved by the v1.2 ModelRouter) take
                precedence when ``NEXUS_USE_MODEL_ROUTER=1``. ``None`` (default)
                leaves model selection to the LLM client / router.
        """
        last_error: Exception | None = None
        # We separate two retry loops: (a) JSON parse retries over the whole
        # response, (b) per-step arg-schema validation retries with a focused
        # re-prompt. The latter only triggers when a tool_registry is wired in
        # AND the parsed plan has a TOOL step whose args fail schema validation.
        for attempt in range(self._max_retries):
            extra = ""
            if attempt > 0 and last_error:
                extra = (
                    f"\n\nPrevious attempt failed: {last_error}"
                    f"\nReturn ONLY valid JSON matching the schema."
                )
            full_system = "\n\n".join(filter(None, [memory_context, SYSTEM_PROMPT]))
            # Forward model_name as `model` kwarg to the LLM client; the
            # client merges it into the request payload (Anthropic provider
            # honors a `model` field in the payload via kwargs.update).
            client_kwargs: dict = {"model_hint": model_hint}
            if model_name is not None:
                client_kwargs["model"] = model_name
            user_msg = f"Task: {task}"
            if spec:
                user_msg += f"\n\nAdditional spec:\n{spec}"
            response = await self._llm.complete(
                system=full_system,
                messages=[{"role": "user", "content": user_msg + extra}],
                **client_kwargs,
            )
            text = response.content[0].text
            try:
                plan = _parse_plan_json(_strip_markdown(text))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                last_error = e
                continue

            # Self-correction loop for arg-schema violations. Only runs when a
            # tool registry is available; otherwise the raw plan is returned
            # unchanged (v1.1 behavior, used by tests that mock only the LLM).
            if self._tool_registry is not None and self._max_arg_schema_retries > 0:
                plan = await self._self_correct_args(
                    plan=plan,
                    task=task,
                    spec=spec,
                    memory_context=memory_context,
                    model_hint=model_hint,
                    model_name=model_name,
                )
            return plan
        raise RuntimeError(f"Planner failed after {self._max_retries} attempts: {last_error}")

    async def _self_correct_args(
        self,
        *,
        plan: Plan,
        task: str,
        spec: str | None,
        memory_context: str,
        model_hint: ModelHint,
        model_name: str | None,
    ) -> Plan:
        """Re-prompt the LLM when a TOOL step's args don't match its schema.

        On success, returns the corrected plan. If validation still fails
        after ``max_arg_schema_retries`` attempts, raises
        ``ArgSchemaValidationError`` — the caller can fall back to ask_user
        or surface the error.
        """
        last_errors = _validate_plan_tool_args(plan, self._tool_registry)
        if not last_errors:
            return plan

        # Flatten the error messages once for the re-prompt; we keep the list
        # around to raise on the final iteration.
        for _ in range(self._max_arg_schema_retries):
            feedback = "\n".join(str(e) for e in last_errors)
            re_prompt = (
                f"Original task: {task}\n\n"
                f"Your previous plan had TOOL steps whose args did not match the "
                f"declared tool schema:\n{feedback}\n\n"
                f"Regenerate the plan with corrected args. Return ONLY valid JSON "
                f"matching the original schema."
            )
            full_system = "\n\n".join(filter(None, [memory_context, SYSTEM_PROMPT]))
            client_kwargs: dict = {"model_hint": model_hint}
            if model_name is not None:
                client_kwargs["model"] = model_name
            response = await self._llm.complete(
                system=full_system,
                messages=[{"role": "user", "content": re_prompt}],
                **client_kwargs,
            )
            text = response.content[0].text
            try:
                plan = _parse_plan_json(_strip_markdown(text))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                # If the re-prompt itself produced garbage, surface that as
                # the new last error and try again.
                last_errors = [
                    ArgSchemaValidationError(
                        step_id="(parser)",
                        tool="(parser)",
                        errors=[f"failed to parse plan: {e}"],
                    )
                ]
                continue
            new_errors = _validate_plan_tool_args(plan, self._tool_registry)
            if not new_errors:
                return plan
            last_errors = new_errors
        # Exhausted self-correction retries — raise so the caller can decide
        # whether to abort, ask the user, or fall back to the legacy path.
        # We raise the first error so the traceback pin-points one step.
        raise last_errors[0]
