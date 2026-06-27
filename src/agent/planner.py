"""Planner - LLM to structured Plan with JSON retry."""
from __future__ import annotations

import json
import re
from typing import Any

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

Constraints:
- 2-10 steps total
- Prefer TOOL steps; VERIFY for test/lint gates; ASK_USER only when truly ambiguous
- Each step must have concrete success_criteria
"""


def _strip_markdown(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


def _parse_plan_json(text: str) -> Plan:
    data = json.loads(text)
    steps = []
    for s in data.get("steps", []):
        steps.append(PlanStep(
            id=s.get("id") or new_step_id(),
            kind=PlanStepKind(s["kind"]),
            intent=s["intent"],
            tool=s.get("tool"),
            args=s.get("args", {}),
            success_criteria=s.get("success_criteria", ""),
            on_failure=OnFailure(s.get("on_failure", "ask")),
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

    async def plan(self, task: str, *, spec: str | None = None) -> Plan:
        user_msg = f"Task: {task}"
        if spec:
            user_msg += f"\n\nAdditional spec:\n{spec}"
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            extra = ""
            if attempt > 0 and last_error:
                extra = f"\n\nPrevious attempt failed: {last_error}\nReturn ONLY valid JSON matching the schema."
            response = await self._llm.complete(
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg + extra}],
            )
            text = response.content[0].text
            try:
                return _parse_plan_json(_strip_markdown(text))
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                last_error = e
                continue
        raise RuntimeError(f"Planner failed after {self._max_retries} attempts: {last_error}")