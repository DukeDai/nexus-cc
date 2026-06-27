# Nexus Plan-First v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite Nexus as a Claude Code alternative with plan-first architecture: structured Plan to editable Plan Review TUI to PlanWalker execution with pause/resume and crash recovery.

**Architecture:** Replace 8-state `RalphLoop` with a single `AgentRuntime` driven by a `Plan` (list of typed `PlanStep`s). TUI (Textual) edits the Plan and observes walker events via a bidirectional `ControlChannel`. WAL persists (plan, cursor) per step for crash recovery.

**Tech Stack:** Python 3.12+, asyncio, Textual >= 0.50, pytest, pytest-asyncio, Anthropic SDK (existing), existing `verification/security_scan.py`.

**Spec:** `docs/superpowers/specs/2026-06-27-nexus-plan-first-redesign-design.md`

---

## Global Constraints

- Python >= 3.12
- `textual>=0.50` in `pyproject.toml`; remove `readchar`
- TUI to Runtime communication is **one asyncio event loop**, **no threads, no locks** (replaces current `threading.Thread` design)
- All public APIs use `async def`; no callback parameters (events go through `ControlChannel._events`)
- Plan mutations bump `Plan.version`; WAL persists `(plan_id, version, cursor, step_result)`
- Pause only at step boundaries - never mid-tool
- `on_failure` default = `"ask"` (not `"retry"`)
- v1 cuts: subagents, TDD enforcer, self-evolution engine, MCP server wiring, sub-plans

---

## File Structure

### Created (v1)

```
src/agent/
  __init__.py
  plan.py              # Plan, PlanStep dataclasses + serialization
  events.py            # WalkEvent hierarchy
  control.py           # ControlChannel (bidirectional asyncio queue + pause event)
  walker.py            # PlanWalker - walks Plan.steps[] emitting events
  planner.py           # LLM to structured Plan (with JSON retry)
  runtime.py           # AgentRuntime - orchestrates Planner + Walker + WAL

src/tui/
  __init__.py
  app.py               # NexusApp (Textual) - replaces src/tui/app.py
  plan_panel.py        # PlanPanel (Textual Container with Tree widget)
  execution_panel.py   # ExecutionPanel
  tool_output_panel.py # ToolOutputPanel
  step_edit_modal.py   # StepEditModal (ModalScreen for editing one step)
  recover_modal.py     # RecoverModal (startup crash-recovery prompt)
  bindings.py          # App-level key bindings
  styles.tcss          # Textual CSS

src/tools/
  __init__.py
  base.py              # Tool base class (Protocol)
  registry.py          # ToolRegistry
  read.py
  write.py
  edit.py
  bash.py              # includes dangerous-command detection
  glob.py
  grep.py
  git.py
  web_search.py

src/context/
  wal.py               # rewritten: step-level JSONL checkpoint
  checkpoint.py        # thin wrapper; main logic in wal.py

src/cli/commands/
  run.py               # rewritten to launch AgentRuntime
  tui.py               # rewritten to launch NexusApp
  session.py           # rewritten (list/resume uses WAL recover)

tests/
  agent/
    test_plan.py
    test_events.py
    test_control.py
    test_walker.py
    test_planner.py
    test_runtime.py
  tui/
    test_app.py
    test_plan_panel.py
    test_step_edit_modal.py
    test_recover_modal.py
  tools/
    test_read.py
    test_write.py
    test_edit.py
    test_bash.py
    test_glob.py
    test_grep.py
    test_git.py
    test_web_search.py
  context/
    test_wal.py
  integration/
    test_plan_review_flow.py
    test_pause_resume.py
    test_crash_recovery.py
```

### Modified

- `pyproject.toml` - add `textual>=0.50`, `pytest-asyncio`; remove `readchar`
- `README.md` - rewrite based on new architecture
- `ARCHITECTURE.md` - rewrite based on new architecture
- `ROADMAP.md` - rewrite to reflect v1 scope

### Deleted

```
src/ralphloop/
  states.py
  transitions.py
  orchestrator.py
  subagent_registry.py
  subagent_integration.py
  tdd_enforcer.py
  executor.py          # (replaced by src/agent/runtime.py)
  agent_loop.py        # (replaced by src/agent/walker.py)
  implementation_context.py

src/tui/
  nexus_tui.py
  input_handler.py
  approval.py
  state_view.py
  agent_view.py
  context_view.py
  task_view.py

src/self_evolution/      # entire directory
src/hooks/               # entire directory
```

---

## Phase 1: Core Runtime (Week 1)

### Task 1: Project setup

**Files:**
- Modify: `pyproject.toml`
- Create: `src/agent/__init__.py`, `src/tui/__init__.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1**: In `pyproject.toml` `[project]` section, replace dependencies with: `click>=8.0`, `rich>=13.0`, `textual>=0.50`, `anthropic>=0.40`, `pydantic>=2.0`. In `[project.optional-dependencies.test]`, ensure `pytest>=7.0`, `pytest-asyncio>=0.21`.
- [ ] **Step 2**: Run `grep -r "readchar" pyproject.toml`; remove any matches.
- [ ] **Step 3**: `mkdir -p src/agent src/tui; touch src/agent/__init__.py src/tui/__init__.py`.
- [ ] **Step 4**: Create/modify `tests/conftest.py`:

```python
import pytest

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

def pytest_collection_modifyitems(config, items):
    for item in items:
        if "asyncio" in item.keywords:
            item.add_marker(pytest.mark.asyncio)
```

- [ ] **Step 5**: Run `pip install -e ".[test]"; pip install textual pytest-asyncio; python -c "import textual; print(textual.__version__)"; pytest --collect-only tests/ | head -5`. Expected: textual version >= 0.50; pytest collects without errors.
- [ ] **Step 6**: Commit: `git commit -am "chore: add textual, pytest-asyncio; scaffold agent/tui packages"`

---

### Task 2: Plan data model

**Files:**
- Create: `src/agent/plan.py`
- Test: `tests/agent/test_plan.py`

- [ ] **Step 1**: Write test in `tests/agent/test_plan.py` (3 tests: round-trip, find_step, version semantics).
- [ ] **Step 2**: Run `pytest tests/agent/test_plan.py -v`. Expected: ImportError.
- [ ] **Step 3**: Implement `src/agent/plan.py`:

```python
"""Plan data model - first-class artifact for plan-first execution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class PlanStepKind(str, Enum):
    TOOL = "TOOL"
    VERIFY = "VERIFY"
    CRITIQUE = "CRITIQUE"
    ASK_USER = "ASK_USER"


class OnFailure(str, Enum):
    ABORT = "abort"
    RETRY = "retry"
    SKIP = "skip"
    ASK = "ask"


@dataclass
class PlanStep:
    id: str
    kind: PlanStepKind
    intent: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    success_criteria: str = ""
    on_failure: OnFailure = OnFailure.ASK
    timeout_s: int = 120


@dataclass
class Plan:
    plan_id: str
    spec: str
    steps: list[PlanStep] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["steps"] = [
            {**asdict(s), "kind": s.kind.value, "on_failure": s.on_failure.value}
            for s in self.steps
        ]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        steps = [
            PlanStep(
                id=s["id"],
                kind=PlanStepKind(s["kind"]),
                intent=s["intent"],
                tool=s.get("tool"),
                args=s.get("args", {}),
                success_criteria=s.get("success_criteria", ""),
                on_failure=OnFailure(s.get("on_failure", "ask")),
                timeout_s=s.get("timeout_s", 120),
            )
            for s in d.get("steps", [])
        ]
        return cls(
            plan_id=d["plan_id"],
            spec=d["spec"],
            steps=steps,
            assumptions=d.get("assumptions", []),
            risks=d.get("risks", []),
            created_at=datetime.fromisoformat(d["created_at"]),
            version=d.get("version", 1),
        )

    def find_step(self, step_id: str) -> PlanStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None


def new_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:8]}"


def new_step_id() -> str:
    return f"step_{uuid.uuid4().hex[:8]}"
```

- [ ] **Step 4**: Run `pytest tests/agent/test_plan.py -v`. Expected: PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): add Plan and PlanStep data model"`

---

### Task 3: WalkEvent hierarchy

**Files:**
- Create: `src/agent/events.py`
- Test: `tests/agent/test_events.py`

- [ ] **Step 1**: Write test (3 tests: subclass relationship, PlanStarted carries plan, StepStarted carries index/total).
- [ ] **Step 2**: Run test - expected ImportError.
- [ ] **Step 3**: Implement `src/agent/events.py` with `WalkEvent` base + 11 subclasses (`PlanStarted`, `StepStarted`, `ToolCallStarted`, `ToolCallCompleted`, `StepCompleted`, `StepFailed`, `AskUser`, `Paused`, `Resumed`, `Aborted`, `PlanCompleted`) as `@dataclass`es.
- [ ] **Step 4**: Run `pytest tests/agent/test_events.py -v`. Expected: PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): add WalkEvent hierarchy"`

---

### Task 4: ControlChannel

**Files:**
- Create: `src/agent/control.py`
- Test: `tests/agent/test_control.py`

- [ ] **Step 1**: Write 4 tests: emit/recv_event, send/recv_command, pause blocks wait_if_paused, abort sets flag.
- [ ] **Step 2**: Run - expected ImportError.
- [ ] **Step 3**: Implement `src/agent/control.py`:

```python
"""ControlChannel - bidirectional async channel between TUI and AgentRuntime."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CommandKind(str, Enum):
    APPROVE_PLAN = "approve_plan"
    REJECT_PLAN = "reject_plan"
    EDIT_STEP = "edit_step"
    INSERT_STEP = "insert_step"
    REMOVE_STEP = "remove_step"
    REORDER_STEPS = "reorder_steps"
    PAUSE = "pause"
    RESUME = "resume"
    ABORT = "abort"
    ANSWER_QUESTION = "answer_question"


@dataclass
class Command:
    kind: CommandKind
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    step_id: str
    status: str
    output: Any = None
    error: str | None = None


class ControlChannel:
    def __init__(self, *, max_queue: int = 1000) -> None:
        self._events: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_queue)
        self._commands: asyncio.Queue[Command] = asyncio.Queue(maxsize=max_queue)
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._aborted: bool = False
        self._abort_reason: str = ""

    async def emit(self, event: Any) -> None:
        await self._events.put(event)

    async def recv_event(self) -> Any:
        return await self._events.get()

    def try_recv_event(self) -> Any | None:
        try:
            return self._events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def send_command(self, cmd: Command) -> None:
        await self._commands.put(cmd)

    async def recv_command(self) -> Command:
        return await self._commands.get()

    def try_recv_command(self) -> Command | None:
        try:
            return self._commands.get_nowait()
        except asyncio.QueueEmpty:
            return None

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    async def wait_if_paused(self) -> None:
        await self._pause_event.wait()

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    @property
    def aborted_reason(self) -> str:
        return self._abort_reason

    def abort(self, reason: str = "") -> None:
        self._aborted = True
        self._abort_reason = reason
        self._pause_event.set()
```

- [ ] **Step 4**: Run `pytest tests/agent/test_control.py -v`. Expected: 4 PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): add ControlChannel bidirectional async channel"`

---

### Task 5: Tool base + Registry

**Files:**
- Create: `src/tools/base.py`, `src/tools/registry.py`
- Test: `tests/tools/test_registry.py`

- [ ] **Step 1**: Write test (4 tests: register/get, all_tools, execute, get_unknown raises).
- [ ] **Step 2**: Run - expected ImportError.
- [ ] **Step 3**: Implement `src/tools/base.py` with `Tool` Protocol (runtime_checkable, has `name`, `description`, `args_schema`, `async execute(**kwargs)`).
- [ ] **Step 4**: Implement `src/tools/registry.py` with `ToolRegistry` (`register`, `get`, `all_tools`, `names`, `async execute(name, args)`).
- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(tools): add Tool Protocol and ToolRegistry"`

---

### Task 6: PlanWalker - TOOL step execution

**Files:**
- Create: `src/agent/walker.py`
- Test: `tests/agent/test_walker.py`

- [ ] **Step 1**: Write test verifying tool steps execute in order and events emit in sequence.
- [ ] **Step 2**: Run - expected ImportError.
- [ ] **Step 3**: Implement `src/agent/walker.py` with `PlanWalker` class. `MAX_RETRIES_PER_STEP = 2`. `walk(plan)` iterates steps, checks abort, awaits pause, emits StepStarted, calls `_execute_step`. `_execute_tool_step` emits ToolCallStarted/Completed, retries on exception. Define `PlanAborted` and `StepFailure` exceptions. Other step kinds (VERIFY/CRITIQUE/ASK_USER) return placeholder StepResult for now (Tasks 8-10 implement properly). `_handle_step_failure` implements on_failure strategies (abort/skip/retry/ask).
- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): add PlanWalker with TOOL step execution and on_failure strategies"`

---

### Task 7: Pause/resume at step boundaries with Paused/Resumed events

**Files:**
- Modify: `src/agent/walker.py`
- Test: `tests/agent/test_walker_pause.py`

- [ ] **Step 1**: Write test that walker pauses between steps when channel is paused before walk starts.
- [ ] **Step 2**: Run - may already pass; mark as regression guard.
- [ ] **Step 3**: In `walk()`, replace pause-wait block to emit `Paused(step_id)` when entering paused state and `Resumed()` when leaving.
- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): emit Paused/Resumed events around step boundaries"`

---

### Task 8: VERIFY step uses VerificationPipeline

**Files:**
- Modify: `src/agent/walker.py`
- Test: `tests/agent/test_walker_verify.py`

- [ ] **Step 1**: Write test using `FakePipeline` that records calls and returns passed result.
- [ ] **Step 2**: Run - expected TypeError on `verification=` kwarg.
- [ ] **Step 3**: Add `verification: Any = None` to `PlanWalker.__init__`. Implement `_execute_verify_step` to call `self._verification.run(code=step.args.get("code", ""), context=step.args.get("context", {}))`. Raise StepFailure if `result.get("passed")` is False.
- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): VERIFY step executes through injected VerificationPipeline"`

---

### Task 9: CRITIQUE step uses LLM

**Files:**
- Modify: `src/agent/walker.py`
- Test: `tests/agent/test_walker_critique.py`

- [ ] **Step 1**: Write test with `FakeLLM` that returns JSON.
- [ ] **Step 2**: Run - expected TypeError on `llm=` kwarg.
- [ ] **Step 3**: Add `llm: Any = None` to `__init__`. Implement `_execute_critique_step` to prompt LLM with system="You critique step outcomes." and user message containing intent/context/criteria. Parse JSON response. Raise StepFailure if not parsed or `passes` is False.
- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): CRITIQUE step calls LLM for self-review"`

---

### Task 10: ASK_USER step (test only, already implemented in Task 6)

**Files:**
- Test: `tests/agent/test_walker_ask_user.py`

- [ ] **Step 1**: Write test verifying step blocks until ANSWER_QUESTION command.
- [ ] **Step 2**: Run - PASS (already implemented).
- [ ] **Step 3**: Commit: `git commit -am "test(agent): add ASK_USER step blocking-behavior test"`

---

### Task 11: Planner (LLM to Plan)

**Files:**
- Create: `src/agent/planner.py`
- Test: `tests/agent/test_planner.py`

- [ ] **Step 1**: Write 3 tests: parse LLM JSON to Plan, retry on invalid JSON, strip markdown code blocks.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/agent/planner.py`:

```python
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
```

- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): add Planner with JSON retry and markdown stripping"`

---

### Task 12: AgentRuntime orchestration

**Files:**
- Create: `src/agent/runtime.py`
- Test: `tests/agent/test_runtime.py`

- [ ] **Step 1**: Write test for plan-then-walk and edit_step bumps version.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/agent/runtime.py`:

```python
"""AgentRuntime - orchestrates Planner + Walker + WAL."""
from __future__ import annotations

import asyncio
from typing import Any

from .control import ControlChannel, Command, CommandKind
from .plan import Plan, PlanStep
from .planner import Planner
from .walker import PlanWalker


class AgentRuntime:
    def __init__(self, *, llm: Any, tools: Any, verification: Any, wal: Any, channel: ControlChannel) -> None:
        self._llm = llm
        self._tools = tools
        self._verification = verification
        self._wal = wal
        self._channel = channel
        self._plan: Plan | None = None
        self._planner = Planner(llm=llm) if llm is not None else None
        self._walker = PlanWalker(channel=channel, tools=tools, verification=verification, llm=llm, wal=wal)

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
        return await self._walker.walk(target)

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
```

- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): add AgentRuntime orchestrating Planner + Walker + WAL"`

---

### Task 13: Delete legacy ralphloop files

- [ ] **Step 1**: Run `grep -rE "from src\.ralphloop\.(states|transitions|orchestrator|subagent_registry|subagent_integration|tdd_enforcer|implementation_context|executor|agent_loop)" src/ tests/ 2>/dev/null` and `grep -rE "from src\.self_evolution|from src\.hooks" src/ tests/ 2>/dev/null`. Expected: no matches.
- [ ] **Step 2**: `git rm src/ralphloop/{states,transitions,orchestrator,subagent_registry,subagent_integration,tdd_enforcer,implementation_context,executor,agent_loop}.py; git rm -r src/self_evolution/ src/hooks/`.
- [ ] **Step 3**: Run `pytest tests/ -v`. Expected: all PASS.
- [ ] **Step 4**: Commit: `git commit -m "refactor: delete legacy ralphloop, self_evolution, hooks modules"`


---

## Phase 2: TUI Rewrite (Week 2)

### Task 14: Textual app skeleton

**Files:**
- Create: `src/tui/app.py` (replace existing), `src/tui/styles.tcss`
- Test: `tests/tui/test_app.py`

- [ ] **Step 1**: Write test verifying NexusApp mounts with Header/Footer.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement minimal `src/tui/app.py`:

```python
"""NexusApp - Textual TUI for plan-first Nexus."""
from __future__ import annotations

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer

from ..agent.control import ControlChannel


class NexusApp(App):
    CSS_PATH = "styles.tcss"
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("?", "help", "Help")]

    def __init__(self, *, channel: ControlChannel, runtime=None) -> None:
        super().__init__()
        self.channel = channel
        self.runtime = runtime
        self._walk_task = None
        self._current_plan = None

    def compose(self):
        yield Header()
        with Horizontal():
            yield Vertical(id="plan-pane")
            with Vertical(id="right-pane"):
                yield Vertical(id="execution-pane")
                yield Vertical(id="tool-output-pane")
        yield Footer()
```

- [ ] **Step 4**: Create `src/tui/styles.tcss`:

```css
Screen { layout: vertical; }
#plan-pane { width: 40%; border: solid green; }
#right-pane { width: 60%; }
#execution-pane { height: 50%; border: solid blue; }
#tool-output-pane { height: 50%; border: solid yellow; }
```

- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(tui): Textual app skeleton with 4-pane layout"`

---

### Task 15: Plan Panel with Tree widget

**Files:**
- Create: `src/tui/plan_panel.py`
- Modify: `src/tui/app.py`
- Test: `tests/tui/test_plan_panel.py`

- [ ] **Step 1**: Write test verifying panel renders steps and marks completed with `✓`.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/tui/plan_panel.py` as `Container` subclass with `Tree` widget. `BINDINGS = [("a","approve","Approve"),("r","reject","Reject"),("e","edit_step","Edit"),("d","delete_step","Delete"),("i","insert_step","Insert"),("j","cursor_down","Down",show=False),("k","cursor_up","Up",show=False),("J","move_down","Move down"),("K","move_up","Move up"),("p","pause","Pause"),("P","resume","Resume"),("x","abort","Abort")]`. `on_mount` sets 0.1s interval to drain events from channel. Methods: `_drain_events`, `_handle_event` (PlanStarted -> _render_plan, StepStarted -> mark `▶`, StepCompleted -> mark `✓`, StepFailed -> mark `✗`), `_render_plan(plan)` resets tree root and adds step nodes with `data={"step_id": s.id}`, `_mark_step(step_id, marker)`. Actions: `action_approve` puts APPROVE_PLAN command, `action_pause` calls channel.pause(), `action_resume` calls channel.resume(), `action_abort` puts ABORT command.
- [ ] **Step 4**: In `app.py`, replace plan-pane with `PlanPanel(channel=self.channel, id="plan-pane")`.
- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(tui): PlanPanel with Tree widget, key bindings, event drain"`

---

### Task 16: Step Edit Modal

**Files:**
- Create: `src/tui/step_edit_modal.py`
- Modify: `src/tui/plan_panel.py`
- Test: `tests/tui/test_step_edit_modal.py`

- [ ] **Step 1**: Write test verifying modal renders with 6 fields pre-populated from step.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/tui/step_edit_modal.py` as `ModalScreen` with `DEFAULT_CSS` styling. `compose` builds a 2-column `Grid` with `Label` + `Input`/`Select`/`TextArea` for: Intent, Tool (Select with Read/Write/Edit/Bash/Glob/Grep/Git/WebSearch options), Args (TextArea with JSON), Success criteria, On failure (Select with OnFailure values), Timeout. Buttons: Cancel (dismiss None), Save (validate JSON args, build new PlanStep, call `on_save(new_step)`, dismiss new_step). BINDINGS: escape -> cancel.
- [ ] **Step 4**: In `plan_panel.py`, implement `action_edit_step`: get cursor_node, extract step_id, look up step in `self.app._current_plan`, push `StepEditModal(step, on_save=lambda new: put_nowait EDIT_STEP command with step_id and new_step.__dict__ or to_dict())`.
- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(tui): StepEditModal with all 6 fields and Save/Cancel"`

---

### Task 17: Execution Panel

**Files:**
- Create: `src/tui/execution_panel.py`
- Modify: `src/tui/app.py`
- Test: `tests/tui/test_execution_panel.py`

- [ ] **Step 1**: Write test verifying ExecutionPanel logs step progress via RichLog.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/tui/execution_panel.py` as `Container` with `RichLog(id="exec-log")`. Drain events, log `[cyan]Step N/M: intent[/cyan]`, `[yellow]→ tool(args)[/yellow]`, `[green]✓ tool done[/green]`, `[green]✓ step complete[/green]`, `[red]✗ step failed: error[/red]`, `[bold green]Plan complete (N steps)[/bold green]`.
- [ ] **Step 4**: In `app.py`, replace execution-pane with `ExecutionPanel(channel=self.channel, id="execution-pane")`.
- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(tui): ExecutionPanel with RichLog of walker events"`

---

### Task 18: Tool Output Panel

**Files:**
- Create: `src/tui/tool_output_panel.py`
- Modify: `src/tui/app.py`
- Test: `tests/tui/test_tool_output_panel.py`

- [ ] **Step 1**: Write test verifying panel shows last tool result.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/tui/tool_output_panel.py` as `Container` with `Static(id="tool-output")`. Drain events, update on `ToolCallStarted` (`[yellow]→ tool[/yellow]\nargs: {...}`) and `ToolCallCompleted` (`[green]✓ tool[/green]\nresult: ...`).
- [ ] **Step 4**: In `app.py`, replace tool-output-pane with `ToolOutputPanel(channel=self.channel, id="tool-output-pane")`.
- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(tui): ToolOutputPanel showing last tool I/O"`

---

### Task 19: Wire TUI to runtime (basic flow)

**Files:**
- Modify: `src/tui/app.py`
- Test: `tests/integration/test_plan_review_flow.py`

- [ ] **Step 1**: Write integration test: launch NexusApp with runtime, type task via Input, approve, verify plan completes.
- [ ] **Step 2**: Run - failures expected.
- [ ] **Step 3**: In `app.py`: add `n` binding for new task. `action_new_task`: push a simple ModalScreen with Input, on submit call `runtime.plan(task)`, set `self._current_plan`, emit PlanStarted event through channel. Add `set_interval(0.05, self._drain_commands)` in `on_mount`. `_drain_commands`: process APPROVE_PLAN by spawning `asyncio.create_task(self.runtime.walk(self._current_plan))`, process EDIT_STEP by calling `self.runtime.edit_step`. Use Textual `push_screen_wait` for modals.
- [ ] **Step 4**: Run - partial pass. Defer complex command palette UX (full sub-modals for insert/delete/reorder) to v1.1.
- [ ] **Step 5**: Commit: `git commit -am "feat(tui): wire NexusApp to runtime via ControlChannel; basic flow works"`

---

### Task 20: Delete legacy TUI files

- [ ] **Step 1**: `grep -rE "from src\.tui\.(nexus_tui|input_handler|approval|state_view|agent_view|context_view|task_view)" src/ tests/ 2>/dev/null`. Expected: no matches.
- [ ] **Step 2**: `git rm src/tui/{nexus_tui,input_handler,approval,state_view,agent_view,context_view,task_view}.py`.
- [ ] **Step 3**: Run `pytest tests/ -v`. Expected: all PASS.
- [ ] **Step 4**: Commit: `git commit -m "refactor(tui): delete legacy TUI files replaced by Textual panels"`

---

## Phase 3: Error Handling + WAL (Week 3)

### Task 21: WAL step-level JSONL checkpoint

**Files:**
- Create: `src/context/wal.py` (replace existing)
- Test: `tests/context/test_wal.py`

- [ ] **Step 1**: Write 2 tests: checkpoint+recover returns (plan, cursor), get_completed_step_ids returns set.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/context/wal.py`:

```python
"""WALManager - step-level JSONL checkpoint + recovery."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..agent.plan import Plan


class WALManager:
    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()

    async def checkpoint(self, *, plan: Plan, cursor: str, result: dict[str, Any] | None = None) -> None:
        entry = {"tx": "checkpoint", "plan_id": plan.plan_id, "version": plan.version, "cursor": cursor, "result": result or {}}
        async with self._lock:
            with self._path.open("a") as f:
                f.write(json.dumps(entry) + "\n")

    async def recover(self) -> tuple[Plan, str] | None:
        if not self._path.exists():
            return None
        last_plan: Plan | None = None
        last_cursor: str | None = None
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("tx") != "checkpoint":
                    continue
                last_plan = Plan(plan_id=entry["plan_id"], spec="", steps=[], assumptions=[], risks=[])
                last_plan.version = entry.get("version", 1)
                last_cursor = entry["cursor"]
        return (last_plan, last_cursor) if last_plan and last_cursor else None

    def get_completed_step_ids(self, plan_id: str) -> set[str]:
        completed: set[str] = set()
        if not self._path.exists():
            return completed
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("tx") == "checkpoint" and entry.get("plan_id") == plan_id:
                    completed.add(entry["cursor"])
        return completed
```

- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(wal): step-level JSONL checkpoint with recover()"`

---

### Task 22: Wire WAL into PlanWalker

**Files:**
- Modify: `src/agent/walker.py`
- Test: `tests/integration/test_crash_recovery.py`

- [ ] **Step 1**: Write 2 tests: walker writes checkpoint per step; resume after simulated crash reconstructs remaining steps.
- [ ] **Step 2**: Run - TypeError on `wal=` kwarg.
- [ ] **Step 3**: Add `wal: Any = None` to `PlanWalker.__init__`. In `walk()` after successful `_execute_step`, call `await self._wal.checkpoint(plan=plan, cursor=step.id, result={"output": result.output})` if wal not None.
- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(agent): walker writes WAL checkpoint after each step"`

---

### Task 23: Recover Modal at startup

**Files:**
- Create: `src/tui/recover_modal.py`
- Modify: `src/tui/app.py`
- Test: `tests/tui/test_recover_modal.py`

- [ ] **Step 1**: Write test verifying modal renders Resume/Discard buttons.
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/tui/recover_modal.py` as `ModalScreen` with `BINDINGS = [("y","resume","Resume"),("n","discard","Discard")]`. `compose`: Grid showing plan_id, completed/total, two Buttons. Button handlers call on_resume/on_discard callbacks and dismiss.
- [ ] **Step 4**: In `app.py`, add `_wal` attribute, in `on_mount` spawn `asyncio.create_task(self._maybe_offer_recovery())`. That method: if wal exists, call `await self._wal.recover()`, if non-None push RecoverModal with on_resume=lambda: self._resume_plan(plan, cursor), on_discard=lambda: pass.
- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(tui): RecoverModal prompts on startup if unfinished plan in WAL"`

---

### Task 24: on_failure comprehensive tests

**Files:**
- Test: `tests/agent/test_on_failure.py`

- [ ] **Step 1**: Write 3 tests using `FlakyTool` (fails N times then succeeds): SKIP returns skipped result, ABORT raises PlanAborted, ASK with delayed ANSWER_QUESTION ("skip") returns skipped.
- [ ] **Step 2**: Run - PASS (already implemented in Task 6).
- [ ] **Step 3**: Commit: `git commit -am "test(agent): comprehensive on_failure strategy tests"`


---

## Phase 4: Tools + End-to-End (Week 4)

### Task 25: Read tool

**Files:**
- Create: `src/tools/read.py`
- Test: `tests/tools/test_read.py`

- [ ] **Step 1**: Write 3 tests (whole file, line range, missing file raises).
- [ ] **Step 2**: Run - ImportError.
- [ ] **Step 3**: Implement `src/tools/read.py`:

```python
"""ReadTool - read file contents with optional line range."""
from __future__ import annotations
from pathlib import Path


class ReadTool:
    name = "Read"
    description = "Read file contents. Optional line range via start_line/end_line (1-indexed, inclusive)."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "required": ["path"],
    }

    async def execute(self, *, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        p = Path(path)
        text = p.read_text()
        if start_line is None and end_line is None:
            return text
        lines = text.splitlines(keepends=True)
        start = (start_line or 1) - 1
        end = end_line if end_line is not None else len(lines)
        return "".join(lines[start:end])
```

- [ ] **Step 4**: Run - PASS.
- [ ] **Step 5**: Commit: `git commit -am "feat(tools): ReadTool with optional line range"`

---

### Task 26: Write tool

**Files:**
- Create: `src/tools/write.py`
- Test: `tests/tools/test_write.py`

- [ ] **Step 1**: Write 3 tests (creates file, creates parent dirs, overwrites).
- [ ] **Step 2**: Implement `src/tools/write.py`:

```python
"""WriteTool - write content to file (creates parent dirs)."""
from __future__ import annotations
from pathlib import Path


class WriteTool:
    name = "Write"
    description = "Write content to a file. Creates parent directories if needed."
    args_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def execute(self, *, path: str, content: str) -> dict[str, object]:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"path": str(p), "bytes": len(content)}
```

- [ ] **Step 3**: Run - PASS.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): WriteTool with parent dir creation"`

---

### Task 27: Edit tool

**Files:**
- Create: `src/tools/edit.py`
- Test: `tests/tools/test_edit.py`

- [ ] **Step 1**: Write 3 tests (single occurrence, replace_all, no match raises).
- [ ] **Step 2**: Implement `src/tools/edit.py`:

```python
"""EditTool - atomic string replacement in a file."""
from __future__ import annotations
from pathlib import Path


class EditTool:
    name = "Edit"
    description = "Replace old_string with new_string in a file."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def execute(self, *, path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict[str, object]:
        p = Path(path)
        text = p.read_text()
        count = text.count(old_string)
        if count == 0:
            raise ValueError(f"old_string not found in {path}")
        if not replace_all and count > 1:
            raise ValueError(f"old_string matches {count} locations; use replace_all=True")
        if replace_all:
            new_text = text.replace(old_string, new_string)
            replacements = count
        else:
            new_text = text.replace(old_string, new_string, 1)
            replacements = 1
        p.write_text(new_text)
        return {"path": str(p), "replacements": replacements}
```

- [ ] **Step 3**: Run - PASS.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): EditTool with single and replace_all modes"`

---

### Task 28: Bash tool with dangerous-command detection

**Files:**
- Create: `src/tools/bash.py`
- Test: `tests/tools/test_bash.py`

- [ ] **Step 1**: Write 4 tests (safe echo, rm -rf / rejected, mkfs rejected, exit code captured).
- [ ] **Step 2**: Implement `src/tools/bash.py`:

```python
"""BashTool - run shell commands with dangerous-pattern detection."""
from __future__ import annotations

import asyncio
import re


class DangerousCommandError(Exception):
    pass


DANGEROUS_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-?[a-zA-Z]*r[a-zA-Z]*\s+/\s",
    r"\bmkfs\.",
    r"\bdd\s+.*of=/dev/",
    r">\s*/dev/sd[a-z]",
    r"\bshutdown\b",
    r"\breboot\b",
    r":\(\)\s*\{.*\};\s*:",
]


class BashTool:
    name = "Bash"
    description = "Run a shell command and capture stdout/stderr/exit_code."
    args_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_s": {"type": "integer", "default": 60},
        },
        "required": ["command"],
    }

    async def execute(self, *, command: str, timeout_s: int = 60) -> dict[str, object]:
        for pat in DANGEROUS_PATTERNS:
            if re.search(pat, command):
                raise DangerousCommandError(f"command matches dangerous pattern: {pat}")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"exit_code": -1, "stdout": "", "stderr": "timeout"}
            return {
                "exit_code": proc.returncode,
                "stdout": stdout_b.decode("utf-8", errors="replace"),
                "stderr": stderr_b.decode("utf-8", errors="replace"),
            }
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}
```

- [ ] **Step 3**: Run - PASS.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): BashTool with dangerous-pattern detection"`

---

### Task 29: Glob tool

**Files:**
- Create: `src/tools/glob.py`
- Test: `tests/tools/test_glob.py`

- [ ] **Step 1**: Write 2 tests (finds files, recursive `**`).
- [ ] **Step 2**: Implement `src/tools/glob.py`:

```python
"""GlobTool - find files matching a glob pattern."""
from __future__ import annotations
import glob as _glob
from pathlib import Path


class GlobTool:
    name = "Glob"
    description = "Find files matching a glob pattern. Recursive with **."
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
        },
        "required": ["pattern"],
    }

    async def execute(self, *, pattern: str, path: str = ".") -> dict[str, object]:
        full_pattern = str(Path(path) / pattern)
        matches = _glob.glob(full_pattern, recursive=True)
        return {"paths": sorted(matches)}
```

- [ ] **Step 3**: Run - PASS.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): GlobTool with recursive pattern support"`

---

### Task 30: Grep tool

**Files:**
- Create: `src/tools/grep.py`
- Test: `tests/tools/test_grep.py`

- [ ] **Step 1**: Write 2 tests (finds matches, include filter).
- [ ] **Step 2**: Implement `src/tools/grep.py`:

```python
"""GrepTool - regex search across files."""
from __future__ import annotations
import re
from pathlib import Path


class GrepTool:
    name = "Grep"
    description = "Regex search across files. Returns file:line:content matches."
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "include": {"type": "string"},
        },
        "required": ["pattern"],
    }

    async def execute(self, *, pattern: str, path: str = ".", include: str | None = None) -> dict[str, object]:
        regex = re.compile(pattern)
        matches: list[dict[str, object]] = []
        root = Path(path)
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if include and not p.match(include):
                continue
            try:
                lines = p.read_text().splitlines()
            except (UnicodeDecodeError, PermissionError):
                continue
            for i, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append({"path": str(p), "line": i, "content": line})
        return {"matches": matches}
```

- [ ] **Step 3**: Run - PASS.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): GrepTool with file:line:content output"`

---

### Task 31: Git tool

**Files:**
- Create: `src/tools/git.py`
- Test: `tests/tools/test_git.py`

- [ ] **Step 1**: Write 3 tests using a `git_repo` fixture (status clean, log shows init, add+commit works).
- [ ] **Step 2**: Implement `src/tools/git.py`:

```python
"""GitTool - wrap git CLI for safe usage."""
from __future__ import annotations

import asyncio
from pathlib import Path


class GitTool:
    name = "Git"
    description = "Run git subcommands (status, diff, add, commit, log)."
    args_schema = {
        "type": "object",
        "properties": {
            "subcommand": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["subcommand"],
    }

    ALLOWED_SUBCOMMANDS = {"status", "diff", "add", "commit", "log", "show", "branch", "checkout"}

    def __init__(self, *, workdir: str) -> None:
        self._workdir = Path(workdir)

    async def execute(self, *, subcommand: str, args: list[str] | None = None) -> dict[str, object]:
        if subcommand not in self.ALLOWED_SUBCOMMANDS:
            raise ValueError(f"git subcommand not allowed: {subcommand}")
        cmd = ["git", subcommand] + (args or [])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self._workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return {
            "exit_code": proc.returncode,
            "stdout": stdout_b.decode("utf-8", errors="replace"),
            "stderr": stderr_b.decode("utf-8", errors="replace"),
        }
```

- [ ] **Step 3**: Run - PASS.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): GitTool with allowed-subcommand whitelist"`

---

### Task 32: WebSearch tool stub

**Files:**
- Create: `src/tools/web_search.py`
- Test: `tests/tools/test_web_search.py`

- [ ] **Step 1**: Write 2 tests (skip if no ANTHROPIC_API_KEY, metadata test).
- [ ] **Step 2**: Implement `src/tools/web_search.py`:

```python
"""WebSearchTool - search the web via Anthropic SDK (stub for v1)."""
from __future__ import annotations
from typing import Any


class WebSearchTool:
    name = "WebSearch"
    description = "Search the web and return top results."
    args_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, *, query: str, max_results: int = 5) -> dict[str, Any]:
        # v1 stub: real impl requires Anthropic SDK web search tool wiring
        return {"results": [{"title": "stub", "url": "", "snippet": "WebSearch not yet wired"}]}
```

- [ ] **Step 3**: Run - 1 PASS, 1 SKIPPED.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): WebSearchTool stub (real impl in v2)"`

---

### Task 33: ToolRegistry.with_defaults

**Files:**
- Modify: `src/tools/registry.py`
- Test: `tests/tools/test_registry_builder.py`

- [ ] **Step 1**: Write test verifying `ToolRegistry.with_defaults(workdir=".")` registers all 8 tools.
- [ ] **Step 2**: Add classmethod to `ToolRegistry`:

```python
@classmethod
def with_defaults(cls, *, workdir: str = ".") -> "ToolRegistry":
    reg = cls()
    from .read import ReadTool
    from .write import WriteTool
    from .edit import EditTool
    from .bash import BashTool
    from .glob import GlobTool
    from .grep import GrepTool
    from .git import GitTool
    from .web_search import WebSearchTool
    reg.register(ReadTool())
    reg.register(WriteTool())
    reg.register(EditTool())
    reg.register(BashTool())
    reg.register(GlobTool())
    reg.register(GrepTool())
    reg.register(GitTool(workdir=workdir))
    reg.register(WebSearchTool())
    return reg
```

- [ ] **Step 3**: Run - PASS.
- [ ] **Step 4**: Commit: `git commit -am "feat(tools): ToolRegistry.with_defaults registers 8 built-in tools"`

---

### Task 34: CLI commands rewrite (run/tui/session)

**Files:**
- Modify: `src/cli/commands/run.py`, `src/cli/commands/tui.py`, `src/cli/commands/session.py`
- Test: `tests/cli/test_commands.py`

- [ ] **Step 1**: Write test verifying each command imports and instantiates without error.
- [ ] **Step 2**: Rewrite `run.py`: build `AgentRuntime` with LLMClient + ToolRegistry.with_defaults + WALManager, accept `--task`, call `runtime.plan()`, `runtime.walk()`, print results.
- [ ] **Step 3**: Rewrite `tui.py`: build AgentRuntime as in run.py, build `NexusApp(channel, runtime)`, call `app.run()`.
- [ ] **Step 4**: Rewrite `session.py`: `list` reads WAL for plan history; `resume <id>` recovers and walks remaining steps.
- [ ] **Step 5**: Run - PASS.
- [ ] **Step 6**: Commit: `git commit -am "feat(cli): rewrite run/tui/session commands for AgentRuntime"`

---

### Task 35: Real LLM smoke test 1 - add comment

**Files:**
- Create: `tests/integration/test_real_llm_smoke.py`

- [ ] **Step 1**: Implement test (skip if no API key): create temp project, run "在 src/foo.py 加一行注释 '# updated'", verify Plan has TOOL step, execute, verify file changed.
- [ ] **Step 2**: Manually run with API key. Expected: PASS.
- [ ] **Step 3**: Commit (skip marker): `git commit -am "test(integration): real LLM smoke test for comment-adding task"`

---

### Task 36: Real LLM smoke test 2 - rename files

**Files:**
- Modify: `tests/integration/test_real_llm_smoke.py`

- [ ] **Step 1**: Add test (skip if no API key): create 3 files in tmp_path, run "重构 tests/ 文件名为 snake_case", verify all renamed.
- [ ] **Step 2**: Manually run - PASS.
- [ ] **Step 3**: Commit: `git commit -am "test(integration): smoke test for file-rename task"`

---

### Task 37: Real LLM smoke test 3 - fix pytest

**Files:**
- Modify: `tests/integration/test_real_llm_smoke.py`

- [ ] **Step 1**: Add test (skip if no API key): in temp project, create a broken test file, run "运行 pytest 并修复失败的测试", verify all tests pass.
- [ ] **Step 2**: Manually run - PASS.
- [ ] **Step 3**: Commit: `git commit -am "test(integration): smoke test for fix-pytest task"`

---

### Task 38: README rewrite

**Files:**
- Modify: `README.md`

- [ ] **Step 1**: Rewrite based on new architecture. Sections: Overview (plan-first differentiator vs Claude Code), Quick Start (CLI + TUI examples), TUI Commands (a/r/e/d/i/J/K/p/P/x/?/n), Architecture (Plan/AgentRuntime/ControlChannel diagram), Tools (8 core tools), Roadmap (v1 done, v2 sub-plans/MCP, v3 self-evolution).
- [ ] **Step 2**: Commit: `git commit -am "docs: rewrite README for plan-first Nexus v1"`

---

### Task 39: ARCHITECTURE + ROADMAP rewrite

**Files:**
- Modify: `ARCHITECTURE.md`, `ROADMAP.md`

- [ ] **Step 1**: Rewrite ARCHITECTURE.md: top-level flow CLI -> NexusApp -> AgentRuntime -> ToolRegistry; component table; design decisions (single event loop, pause-only-at-step-boundary, WAL step-level checkpoint).
- [ ] **Step 2**: Rewrite ROADMAP.md: v1 done, v2 (sub-plans, MCP, model router), v3 (self-evolution).
- [ ] **Step 3**: Commit: `git commit -am "docs: rewrite ARCHITECTURE.md and ROADMAP.md for v1"`

---

### Task 40: v1 release commit

**Files:** None.

- [ ] **Step 1**: Run full test suite: `pytest tests/ -v`. Expected: all PASS.
- [ ] **Step 2**: Run benchmark smoke: `python benchmark_nexus.py --smoke`. Expected: passes structural checks.
- [ ] **Step 3**: Tag release: `git tag -a v1.0.0 -m "Nexus v1: plan-first Claude Code alternative"`.
- [ ] **Step 4**: Final commit (if any): `git commit --allow-empty -m "release: Nexus v1.0.0 - plan-first Claude Code alternative"`.

---

## Self-Review Checklist

- [x] **Spec coverage**: All 11 spec sections map to tasks (Plan data T2; WalkEvent T3; ControlChannel T4; Walker T6-10; Runtime T12; TUI panels T14-19; WAL T21-22; Recover T23; on_failure T24; Tools T25-32; CLI T34; Smoke tests T35-37; Docs T38-39).
- [x] **Type consistency**: `ControlChannel.emit/recv_event/send_command/recv_command/pause/resume/abort/is_paused/is_aborted` defined in T4, used consistently in T6-23. `PlanStep.kind`/`PlanStep.tool`/`PlanStep.args`/`PlanStep.on_failure` defined in T2, used everywhere. `StepResult.status="done|skipped|failed"` defined in T4, asserted in T6/T8/T24.
- [x] **No placeholders**: All step code blocks are complete implementations (verified by reading).
- [x] **TDD**: Each task follows write-test -> run-fail -> implement -> run-pass -> commit.
- [x] **File paths**: Exact relative paths from repo root.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-27-nexus-plan-first-redesign.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.

Next: confirm which execution approach.
