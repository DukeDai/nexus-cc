"""Planner - LLM to structured Plan with JSON retry."""
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
"""


def _strip_markdown(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


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


class Planner:
    def __init__(self, *, llm: Any, max_retries: int = 3) -> None:
        self._llm = llm
        self._max_retries = max_retries

    async def plan(
        self,
        task: str,
        *,
        spec: str | None = None,
        memory_context: str = "",
        model_hint: ModelHint = ModelHint.PLANNER,
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
        """
        user_msg = f"Task: {task}"
        if spec:
            user_msg += f"\n\nAdditional spec:\n{spec}"
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            extra = ""
            if attempt > 0 and last_error:
                extra = f"\n\nPrevious attempt failed: {last_error}\nReturn ONLY valid JSON matching the schema."
            full_system = "\n\n".join(filter(None, [memory_context, SYSTEM_PROMPT]))
            response = await self._llm.complete(
                system=full_system,
                messages=[{"role": "user", "content": user_msg + extra}],
                model_hint=model_hint,
            )
            text = response.content[0].text
            try:
                return _parse_plan_json(_strip_markdown(text))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                last_error = e
                continue
        raise RuntimeError(f"Planner failed after {self._max_retries} attempts: {last_error}")