"""AgentRuntime - orchestrates Planner + Walker + WAL."""
from __future__ import annotations

import asyncio
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
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._verification = verification
        self._wal = wal
        self._channel = channel
        self.role_registry = role_registry
        self._plan: Plan | None = None
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
        plan = await self._planner.plan(task, spec=spec)
        self._plan = plan
        return plan

    async def walk(self, plan: Plan | None = None) -> list[Any]:
        target = plan or self._plan
        if target is None:
            raise RuntimeError("No plan to walk")
        self._plan = target
        self._walker._runtime = self
        return await self._walker.walk(target)

    async def plan_subplan(
        self,
        role: "AgentRole",
        definition: "RoleDefinition",
        task: str,
        context: dict | None = None,
    ) -> Plan:
        """Generate a sub-plan for the given role.

        Args:
            role: The agent role (must match definition.role).
            definition: The role configuration.
            task: Natural-language task description.
            context: Optional context dict.

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
        plan = await self._planner.plan(task, spec=spec_content)
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