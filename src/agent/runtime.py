"""AgentRuntime - orchestrates Planner + Walker + WAL."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .control import ControlChannel, Command, CommandKind
from .plan import Plan, PlanStep
from .planner import Planner
from .walker import PlanWalker

if TYPE_CHECKING:
    from src.agents.base import AgentRole
    from src.agents.registry import RoleDefinition, RoleRegistry


class AgentRuntime:
    def __init__(
        self,
        *,
        llm: Any,
        tools: Any,
        verification: Any,
        wal: Any,
        channel: ControlChannel,
        role_registry: "RoleRegistry | None" = None,
        memory_store: Any = None,
        evolver: Any = None,
        prompt_registry: Any = None,
        workdir: Path | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._verification = verification
        self._wal = wal
        self._channel = channel
        self.role_registry = role_registry
        self._plan: Plan | None = None
        self._memory_store = memory_store
        self._evolver = evolver
        self._prompt_registry = prompt_registry
        self._workdir = Path(workdir) if workdir else Path(".")
        # Wire the ToolRegistry into the Planner so it can validate TOOL-step
        # args against each tool's args_schema and self-correct on mismatch.
        # If the injected ``tools`` doesn't expose ``args_schema`` (custom
        # non-tool registry), Planner gracefully no-ops the validation.
        try:
            self._planner = (
                Planner(llm=llm, tool_registry=tools) if llm is not None else None
            )
        except TypeError:
            # Backwards-compat: tests may pass an object that isn't a
            # ToolRegistry; fall back to the legacy v1.1 path.
            self._planner = Planner(llm=llm) if llm is not None else None
        self._walker = PlanWalker(
            channel=channel,
            tools=tools,
            verification=verification,
            llm=llm,
            wal=wal,
            role_registry=role_registry,
        )

    async def plan(self, task: str, *, spec: str | None = None) -> Plan:
        if self._planner is None:
            raise RuntimeError("Planner requires LLM client")
        memory_context = ""
        if self._memory_store is not None:
            memory_context = self._memory_store.planner_context(task, k=5)
        plan = await self._planner.plan(task, spec=spec, memory_context=memory_context)
        self._plan = plan
        return plan

    async def walk(self, plan: Plan | None = None) -> list[Any]:
        target = plan or self._plan
        if target is None:
            raise RuntimeError("No plan to walk")
        self._plan = target
        self._walker._runtime = self
        try:
            result = await self._walker.walk(target)
        except Exception as e:
            if self._evolver:
                self._evolver.record_outcome(target, results=getattr(self._walker, "_step_results", []))
                self._stage_evolver_changes()
            raise
        if self._evolver:
            self._evolver.record_outcome(target, results=getattr(self._walker, "_step_results", []))
            self._stage_evolver_changes()
        return result

    def _stage_evolver_changes(self) -> None:
        if not self._evolver or not self._prompt_registry:
            return
        staged = self._evolver.update_prompt_registry(self._prompt_registry)
        if staged.changes:
            path = self._workdir / ".nexus" / "prompts" / "staged.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "changes": {n: asdict(t) for n, t in staged.changes.items()},
                "rationale": staged.rationale,
                "created_at": staged.created_at.isoformat(),
            }, default=str))

    async def plan_subplan(
        self,
        role: "AgentRole",
        definition: "RoleDefinition",
        task: str,
        context: dict | None = None,
        model_name: str | None = None,
    ) -> Plan:
        """Generate a sub-plan for the given role.

        Args:
            role: The agent role (must match definition.role).
            definition: The role configuration.
            task: Natural-language task description.
            context: Optional context dict.
            model_name: Optional v1.2 model-name hint derived from the role's
                ``model_tier`` (FAST -> claude-haiku-4-5, SONNET ->
                claude-sonnet-4-6, OPUS -> claude-opus-4-8). When provided,
                this is the *initial* model selection; the planner / ModelPolicy
                may still resolve a different name from
                ``.nexus/policy.yaml`` (``per_role`` section) before the LLM is
                called. ``None`` (default) preserves pre-v1.2 behavior — the
                underlying LLM client uses its own default. The
                ``NEXUS_USE_MODEL_ROUTER`` env var gates whether the hint is
                actually consumed end-to-end (when unset, model_name has no
                effect on routing).

        Returns:
            A new Plan ready to be walked. Max step count is capped at
            definition.max_subplan_steps; if exceeded, raises ValueError.

        Raises:
            RuntimeError: If runtime was constructed without an LLM.
            ValueError: If generated sub-plan exceeds max_subplan_steps.
        """
        if self._planner is None:
            raise RuntimeError("Planner requires LLM client")
        # Use role's system_prompt as the spec for Planner.plan
        spec_content = definition.system_prompt
        # ``model_name`` is propagated as a hint; Planner.plan forwards it
        # to the underlying LLMClient.complete(..., model=...) which honors
        # it for the request payload. When the feature flag
        # ``NEXUS_USE_MODEL_ROUTER=1`` is set, the ``_RouterAdapter`` is in
        # control and resolves a final model name via ``ModelPolicy`` (the
        # policy.yaml ``per_role`` section can still override this tier
        # default). When unset, the hint is a no-op and the legacy LLM
        # client uses its own default model — same as pre-v1.2.
        plan_kwargs: dict = {"spec": spec_content}
        if model_name is not None:
            plan_kwargs["model_name"] = model_name
        plan = await self._planner.plan(task, **plan_kwargs)
        if len(plan.steps) > definition.max_subplan_steps:
            raise ValueError(
                f"Sub-plan has {len(plan.steps)} steps, exceeds "
                f"max_subplan_steps={definition.max_subplan_steps} for role {role.name}"
            )
        return plan

    def pause(self) -> None: self._channel.pause()
    def resume(self) -> None: self._channel.resume()
    def abort(self, reason: str = "") -> None: self._channel.abort(reason)

    def edit_step(self, step_id: str, new_step: PlanStep) -> None:
        if self._plan is None: return
        for i, s in enumerate(self._plan.steps):
            if s.id == step_id:
                self._plan.steps[i] = new_step
                self._plan.version += 1
                return

    def insert_step(self, after_id: str, new_step: PlanStep) -> None:
        if self._plan is None: return
        for i, s in enumerate(self._plan.steps):
            if s.id == after_id:
                self._plan.steps.insert(i + 1, new_step)
                self._plan.version += 1
                return
        self._plan.steps.append(new_step)
        self._plan.version += 1

    def remove_step(self, step_id: str) -> None:
        if self._plan is None: return
        self._plan.steps = [s for s in self._plan.steps if s.id != step_id]
        self._plan.version += 1

    def reorder_steps(self, ordered_ids: list[str]) -> None:
        if self._plan is None: return
        by_id = {s.id: s for s in self._plan.steps}
        self._plan.steps = [by_id[i] for i in ordered_ids if i in by_id]
        self._plan.version += 1

    def answer_question(self, step_id: str, answer: str) -> None:
        asyncio.create_task(self._channel.send_command(Command(
            kind=CommandKind.ANSWER_QUESTION,
            payload={"step_id": step_id, "answer": answer},
        )))