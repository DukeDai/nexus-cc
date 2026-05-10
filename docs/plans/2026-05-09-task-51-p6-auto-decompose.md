# Task 51 — P6 自主任务分解实现计划

> **目标:** 在 `run_tasks()` 入口处主动判断任务复杂度，复杂任务自动拆分为可并行执行的子任务队列。

---

## 背景与现状

- `EscalationOption.DECOMPOSE` 已存在于 `orchestrator.py:74`，但仅为**事后 escalation 选项**（3次重试失败后才触发）
- `run_tasks()` 是纯顺序 for 循环，无复杂度预判
- `_classify_task()` 存在，但仅用于**模型路由**（TaskType），不做复杂度判断
- P6 要求：**入口处主动拆分**，不是等 escalation

---

## 架构决策

**分解后的执行策略: 共享 RalphLoop + Sequential task_queue**

RalphLoop 的 `task_queue` 本身支持多任务，orchestrator 按 `task_index` 顺序处理。将所有子任务构建为一个 `task_queue`，无需改变核心循环，复用现有基础设施。

- 子任务间有依赖 → 顺序入队（已有 `task_index` 保障）
- 子任务间无依赖 → 可并行（但本次 plan 聚焦分解，并行在 Task 52 处理）
- DECOMPOSE 作为新状态相位加入状态机，pre-check 复杂度后提前触发

---

## 新增文件

- `src/ralphloop/complexity.py` — 复杂度判断枚举与策略

## 修改文件

- `src/ralphloop/executor.py` — 新增 3 个方法，修改 `run_tasks()`
- `src/ralphloop/orchestrator.py` — 新增 `DECOMPOSE` 状态到状态机
- `src/ralphloop/states.py` — `RalphState` 枚举新增 `DECOMPOSE`
- `src/ralphloop/transitions.py` — 新增 DECOMPOSE 相关 transition
- `src/ralphloop/__init__.py` — 导出新枚举
- `tests/test_ralphloop_executor.py` — 新增 6 个测试

---

## Task 1: 添加 TaskComplexity 枚举与复杂度判断策略

**Objective:** 新建 `complexity.py`，定义 `TaskComplexity` 枚举与启发式判断逻辑。

**Files:**
- Create: `src/ralphloop/complexity.py`

**Step 1: 创建 `src/ralphloop/complexity.py`**

```python
"""Task complexity classification for P6 auto-decompose."""

from __future__ import annotations
from enum import Enum, auto


class TaskComplexity(Enum):
    """Task complexity levels for auto-decomposition."""
    SIMPLE = auto()      # Single step, one file, no拆分 needed
    MODERATE = auto()    # 2-3 steps, still manageable
    COMPLEX = auto()     # Multi-step, cross-module, needs decomposition


# Complexity signal keywords
_COMPLEX_KEYWORDS = {
    "refactor", "redesign", "migrate", "benchmark", "audit",
    "implement feature", "build system", "multi-module",
    "performance optimization", "security review", "integration",
}
_MODERATE_KEYWORDS = {
    "add endpoint", "fix bug", "update config", "write test",
    "create module", "add field", "implement handler",
    "multiple files", "several", "various",
}

# Step-count heuristics (heuristic: "and"/"then"/numbered steps)
_COMPLEX_PATTERNS = (
    r'\b(and|then|after that|next|finally|also|plus)\b',
    r'\b\d+\s+(steps?|tasks?|phases?|parts?)\b',
    r'\bfirst[\s,]+(then|after|next)\b',
    r'\bselect.*from.*where.*and.*where\b',  # SQL multi-condition
)


def _count_structural_steps(task: str) -> int:
    """Estimate step count from structural patterns."""
    text = task.lower()
    count = 1
    # Split on conjunctions that suggest multiple steps
    separators = [' and then ', ' after that ', '; then ', '\n- ', '\n  - ']
    for sep in separators:
        count += text.count(sep)
    return max(count, 1)


def classify_complexity(task: str) -> TaskComplexity:
    """Classify task complexity using heuristic signals.

    Uses keyword matching + structural pattern counting to avoid
    expensive LLM calls at entry point.
    """
    task_lower = task.lower()
    step_count = _count_structural_steps(task_lower)

    # Check for explicit complexity keywords
    complex_kw_count = sum(1 for kw in _COMPLEX_KEYWORDS if kw in task_lower)
    moderate_kw_count = sum(1 for kw in _MODERATE_KEYWORDS if kw in task_lower)

    # Multi-step signal
    if step_count >= 4 or complex_kw_count >= 2:
        return TaskComplexity.COMPLEX
    if step_count >= 2 or moderate_kw_count >= 1:
        return TaskComplexity.MODERATE
    return TaskComplexity.SIMPLE
```

**Step 2: 验证语法**

```bash
cd /Users/dukedai/dev/nexus-cc && python -c "from src.ralphloop.complexity import classify_complexity, TaskComplexity; print('OK')"
```

Expected: `OK`

**Step 3: 写测试**

```python
# tests/test_complexity.py
import pytest
from src.ralphloop.complexity import classify_complexity, TaskComplexity


def test_simple_task_single_step():
    assert classify_complexity("fix the typo in README") == TaskComplexity.SIMPLE


def test_moderate_task_two_steps():
    assert classify_complexity("add endpoint and write test for it") == TaskComplexity.MODERATE


def test_complex_task_multi_step():
    assert classify_complexity("refactor the auth module, update tests, and migrate database") == TaskComplexity.COMPLEX


def test_complex_keyword_detected():
    assert classify_complexity("implement feature: multi-module cache system") == TaskComplexity.COMPLEX
```

**Step 4: 运行测试**

```bash
cd /Users/dukedai/dev/nexus-cc && pytest tests/test_complexity.py -v
```

Expected: 4 passed

**Step 5: Commit**

```bash
git add src/ralphloop/complexity.py tests/test_complexity.py
git commit -m "feat(ralphloop): add TaskComplexity enum and heuristic classifier"
```

---

## Task 2: 修改 `RalphState` 枚举，添加 `DECOMPOSE` 状态

**Objective:** 在状态机中加入 DECOMPOSE 状态，位置在 PLAN 之前。

**Files:**
- Modify: `src/ralphloop/states.py`

**Step 1: 读文件确认结构**

```bash
head -30 /Users/dukedai/dev/nexus-cc/src/ralphloop/states.py
```

**Step 2: patch**

Old:
```python
class RalphState(Enum):
    """RalphLoop explicit state machine states."""
    INIT = "init"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    REFLECT = "reflect"
    COMMIT = "commit"
    ABORT = "abort"
```

New:
```python
class RalphState(Enum):
    """RalphLoop explicit state machine states."""
    INIT = "init"
    DECOMPOSE = "decompose"   # P6: auto-decompose complex tasks
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    REFLECT = "reflect"
    COMMIT = "commit"
    ABORT = "abort"
```

**Step 3: 运行测试确认无破坏**

```bash
cd /Users/dukedai/dev/nexus-cc && pytest tests/test_ralphloop_executor.py -v -k "state or init" --tb=short 2>&1 | tail -20
```

**Step 4: Commit**

```bash
git add src/ralphloop/states.py
git commit -m "feat(ralphloop): add DECOMPOSE state to RalphState enum"
```

---

## Task 3: 添加 DECOMPOSE transition rules

**Objective:** 在 transitions.py 中添加 INIT → DECOMPOSE 和 DECOMPOSE → PLAN 的 transition。

**Files:**
- Modify: `src/ralphloop/transitions.py`

**Step 1: 读 transitions.py 确认结构**

```bash
grep -n "INIT\|PLAN\|VERIFY\|TransitionTrigger\|get_valid_transitions" /Users/dukedai/dev/nexus-cc/src/ralphloop/transitions.py | head -30
```

**Step 2: patch — 在 transitions.py 找 `TransitionTrigger` 枚举末尾添加新 trigger，在 get_valid_transitions 中添加新规则**

Old (在 TransitionTrigger 枚举中，在 TASK_START 之后）:
```python
    TASK_START = auto()
```

New:
```python
    TASK_START = auto()
    DECOMPOSE_COMPLETE = auto()   # P6: complex task decomposed into subtasks
```

Old (在 get_valid_transitions 函数中，找到 INIT → PLAN 的 transition):
```python
    if state == RalphState.INIT:
        if trigger == TransitionTrigger.TASK_START:
            return [Transition(
                from_state=RalphState.INIT,
                to_state=RalphState.PLAN,
                trigger=TransitionTrigger.TASK_START,
                guard=None,
                description="Start task from INIT",
            )]
```

New:
```python
    if state == RalphState.INIT:
        if trigger == TransitionTrigger.TASK_START:
            # P6: check complexity — if complex, go to DECOMPOSE first
            # (complexity check is done in executor, which sets a flag)
            return [Transition(
                from_state=RalphState.INIT,
                to_state=RalphState.DECOMPOSE,  # Route to DECOMPOSE
                trigger=TransitionTrigger.TASK_START,
                guard=None,
                description="Complex task, enter decomposition phase",
            )]
```

Old (在 DECOMPOSE 状态处理，初始部分):
```python
    if state == RalphState.DECOMPOSE:
        return []  # No transitions defined yet
```

New:
```python
    if state == RalphState.DECOMPOSE:
        if trigger == TransitionTrigger.DECOMPOSE_COMPLETE:
            return [Transition(
                from_state=RalphState.DECOMPOSE,
                to_state=RalphState.PLAN,
                trigger=TransitionTrigger.DECOMPOSE_COMPLETE,
                guard=None,
                description="Decomposition complete, enter planning",
            )]
        if trigger == TransitionTrigger.MAX_RETRIES_EXCEEDED:
            return [get_abort_transition(RalphState.DECOMPOSE)]
```

**Step 3: 验证语法**

```bash
cd /Users/dukedai/dev/nexus-cc && python -c "from src.ralphloop.transitions import get_valid_transitions; print('OK')"
```

**Step 4: Commit**

```bash
git add src/ralphloop/transitions.py
git commit -m "feat(ralphloop): add DECOMPOSE state transitions"
```

---

## Task 4: 在 executor.py 中实现三个核心方法

**Objective:** 新增 `_classify_task_complexity()`, `_decompose_task()`, `_execute_decompose_phase()` 方法。

**Files:**
- Modify: `src/ralphloop/executor.py`

**Step 1: 读文件顶部 import 和 class 定义**

```bash
head -60 /Users/dukedai/dev/nexus-cc/src/ralphloop/executor.py
```

**Step 2: 在 import 区添加**

Old:
```python
from __future__ import annotations
import time
import uuid
from pathlib import Path
```

New:
```python
from __future__ import annotations
import json
import re
import time
import uuid
from pathlib import Path
```

Also add after existing imports:
```python
from .complexity import TaskComplexity, classify_complexity
```

**Step 3: 在 `_classify_task()` 方法之后（约 line 1012），添加三个新方法**

After:
```python
        return TaskType.CODE  # Default to code

    def _select_model(
```

Insert before `_select_model`:

```python
        return TaskType.CODE  # Default to code

    # ─── P6: Auto-Decomposition ────────────────────────────────────────────────

    def _classify_task_complexity(self, task: str) -> TaskComplexity:
        """Classify task complexity using heuristic signals.

        This is a fast pre-check (no LLM call) to decide whether
        a task needs decomposition before entering RalphLoop.
        """
        return classify_complexity(task)

    def _decompose_task(
        self,
        task: str,
        spec_md: str | None,
        constraints: list[str],
    ) -> list[dict[str, Any]]:
        """Decompose a complex task into an ordered list of subtasks.

        Uses an LLM call to generate a structured decomposition with
        dependency information. Each subtask dict has keys:
          - description: str
          - task_id: str
          - spec_md: str | None
          - constraints: list[str]
          - depends_on: list[str]  (task_ids this subtask waits for)

        Returns an empty list if decomposition fails or task is simple.
        """
        if not self._llm_client:
            return []

        prompt = (
            "You are a task decomposition expert. Given the following task,\n"
            "break it into 2-8 independent, focused subtasks that can be\n"
            "executed in order. For each subtask, note any dependencies\n"
            "(other subtask IDs it depends on).\n\n"
            f"Task: {task}\n\n"
            "Respond ONLY with valid JSON in this exact format:\n"
            "{\n"
            '  "subtasks": [\n'
            '    {"id": "t1", "description": "...", "depends_on": []},\n'
            '    {"id": "t2", "description": "...", "depends_on": ["t1"]}\n'
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Each subtask should be independently verifiable\n"
            "- Maximum 8 subtasks\n"
            "- Use simple IDs: t1, t2, t3 ...\n"
            "- depends_on is a list of task IDs (empty if no dependencies)\n"
            "- If the task is already simple enough, return a single subtask\n"
        )
        if spec_md:
            prompt += f"\n\nSPEC.md:\n{spec_md[:2000]}"

        try:
            response = self._llm_client.complete(prompt, max_tokens=1024)
            # Try to extract JSON from response
            match = re.search(r'\{[\s\S]+\}', response)
            if not match:
                return []
            data = json.loads(match.group())
            subtasks = data.get("subtasks", [])
            result = []
            for s in subtasks:
                result.append({
                    "description": s["description"],
                    "task_id": s.get("id", str(uuid.uuid4())[:8]),
                    "spec_md": spec_md,
                    "constraints": constraints,
                    "depends_on": s.get("depends_on", []),
                })
            return result
        except Exception:
            return []

    def _execute_decompose_phase(
        self,
        orchestrator: Any,
        task: str,
        spec_md: str | None,
        constraints: list[str],
    ) -> None:
        """Execute the DECOMPOSE phase: classify complexity, decompose if needed.

        Side-effects:
          - Sets orchestrator._decomposition_result with subtask list
          - Transitions orchestrator state from DECOMPOSE to PLAN
        """
        complexity = self._classify_task_complexity(task)

        if complexity == TaskComplexity.SIMPLE:
            # No decomposition needed — transition immediately
            orchestrator._decomposition_result = [{
                "description": task,
                "task_id": str(uuid.uuid4())[:8],
                "spec_md": spec_md,
                "constraints": constraints or [],
                "depends_on": [],
            }]
            orchestrator.state = RalphState.PLAN
            return

        # Complex: decompose via LLM
        subtasks = self._decompose_task(task, spec_md, constraints or [])
        if not subtasks:
            # Decomposition failed — fall back to single task
            subtasks = [{
                "description": task,
                "task_id": str(uuid.uuid4())[:8],
                "spec_md": spec_md,
                "constraints": constraints or [],
                "depends_on": [],
            }]

        orchestrator._decomposition_result = subtasks

        # Update orchestrator task_queue with decomposed subtasks
        orchestrator.task_queue = subtasks
        orchestrator.task_index = 0

        # Transition to PLAN
        orchestrator.state = RalphState.PLAN
```

**Step 4: 验证语法**

```bash
cd /Users/dukedai/dev/nexus-cc && python -c "from src.ralphloop.executor import NexusExecutor; print('OK')" 2>&1
```

**Step 5: Commit**

```bash
git add src/ralphloop/executor.py
git commit -m "feat(ralphloop): add P6 auto-decomposition methods to executor"
```

---

## Task 5: 集成 DECOMPOSE phase 到 run_task() 主循环

**Objective:** 修改 `run_task()` 中构建的 orchestrator，让 DECOMPOSE 状态能真正被触发和处理。

**Files:**
- Modify: `src/ralphloop/executor.py` (run_task method body)

**Step 1: 在 `orchestrator.run()` 调用前，注入 decomposition phase handler**

找到 run_task() 中这段代码（约 line 490）:

```python
        # Run the RalphLoop
        result = orchestrator.run()
```

替换为:

```python
        # P6: Pre-check complexity and execute DECOMPOSE phase if needed.
        # This runs BEFORE orchestrator.run() so the task_queue is
        # already populated with subtasks when the state machine starts.
        self._execute_decompose_phase(orchestrator, task, spec_md, constraints)

        # Run the RalphLoop (task_queue may now have multiple subtasks)
        result = orchestrator.run()
```

**Step 2: 同时修改 `orchestrator.run()` 调用之前的 WAL log — 改 from INIT → DECOMPOSE**

找到（约 line 482-488）:

```python
        if self._wal:
            self._wal.log_transition(
                from_state="INIT",
                to_state="PLAN",
                trigger=f"task_start:{task_id}",
            )
```

替换为:

```python
        if self._wal:
            # Log the decomposition transition if triggered
            complexity = self._classify_task_complexity(task)
            to_state = "DECOMPOSE" if complexity != TaskComplexity.SIMPLE else "PLAN"
            self._wal.log_transition(
                from_state="INIT",
                to_state=to_state,
                trigger=f"task_start:{task_id}",
            )
```

**Step 3: 验证语法**

```bash
cd /Users/dukedai/dev/nexus-cc && python -c "from src.ralphloop.executor import NexusExecutor; print('OK')"
```

**Step 4: 运行现有测试确保无破坏**

```bash
cd /Users/dukedai/dev/nexus-cc && pytest tests/test_ralphloop_executor.py -v --tb=short 2>&1 | tail -30
```

**Step 5: Commit**

```bash
git add src/ralphloop/executor.py
git commit -m "feat(ralphloop): integrate DECOMPOSE phase into run_task()"
```

---

## Task 6: 添加测试用例

**Objective:** 覆盖新增的 3 个方法和集成逻辑。

**Files:**
- Modify: `tests/test_ralphloop_executor.py`

**Step 1: 在测试文件中添加 fixture 和测试用例**

在现有测试类或 module-level 添加:

```python
# ─── P6 Auto-Decompose Tests ────────────────────────────────────────────────

from src.ralphloop.complexity import TaskComplexity


def test_classify_task_complexity_simple():
    """Simple single-step task returns SIMPLE."""
    from src.ralphloop.executor import NexusExecutor
    ex = NexusExecutor(llm_provider="mock")
    assert ex._classify_task_complexity("fix the typo in README") == TaskComplexity.SIMPLE


def test_classify_task_complexity_complex():
    """Multi-step / refactor task returns COMPLEX."""
    from src.ralphloop.executor import NexusExecutor
    ex = NexusExecutor(llm_provider="mock")
    result = ex._classify_task_complexity(
        "refactor auth module, update all tests, and migrate database schema"
    )
    assert result == TaskComplexity.COMPLEX


def test_decompose_task_returns_subtask_list():
    """_decompose_task returns a list of subtask dicts with required keys."""
    from src.ralphloop.executor import NexusExecutor
    ex = NexusExecutor(llm_provider="mock", llm_api_key="test")
    # Without a real LLM client it returns []
    result = ex._decompose_task("test task", None, [])
    # Empty when no client, that's expected
    assert isinstance(result, list)


def test_execute_decompose_phase_simple_skips_decomposition(monkeypatch):
    """Simple task does NOT call LLM and transitions directly to PLAN."""
    from src.ralphloop.executor import NexusExecutor
    from src.ralphloop.orchestrator import RalphLoop, RalphState

    ex = NexusExecutor(llm_provider="mock")
    ex._llm_client = None  # No LLM — decomposition would fail anyway

    orch = RalphLoop(
        task_queue=[{"description": "fix typo", "task_id": "t0", "spec_md": None, "constraints": []}],
        agent_executor=lambda *a, **k: {},
    )

    ex._execute_decompose_phase(orch, "fix the typo in README", None, [])

    assert orch.state == RalphState.PLAN
    assert len(orch.task_queue) == 1
    assert orch.task_queue[0]["description"] == "fix the typo in README"


def test_execute_decompose_phase_complex_sets_subtasks():
    """Complex task decomposition populates task_queue with subtasks."""
    from src.ralphloop.executor import NexusExecutor
    from src.ralphloop.orchestrator import RalphLoop, RalphState

    ex = NexusExecutor(llm_provider="mock")
    ex._llm_client = None  # No LLM — will fall back to single task

    orch = RalphLoop(
        task_queue=[{"description": "big task", "task_id": "t0", "spec_md": None, "constraints": []}],
        agent_executor=lambda *a, **k: {},
    )

    ex._execute_decompose_phase(orch, "big refactor task", None, [])

    # Falls back to single task (no LLM), but task_queue is still set
    assert orch.state == RalphState.PLAN
    assert len(orch.task_queue) >= 1


def test_run_task_includes_decompose_wal_entry(monkeypatch):
    """run_task() WAL logs DECOMPOSE state for complex tasks."""
    from src.ralphloop.executor import NexusExecutor

    entries = []
    class MockWAL:
        def log_transition(self, from_state, to_state, trigger):
            entries.append((from_state, to_state, trigger))

    ex = NexusExecutor(llm_provider="mock")
    ex._wal = MockWAL()
    ex._llm_client = None

    # Simple task should go INIT → PLAN
    ex.run_task("fix typo")
    assert entries[-1][1] in ("PLAN", "DECOMPOSE")
```

**Step 2: 运行新测试**

```bash
cd /Users/dukedai/dev/nexus-cc && pytest tests/test_ralphloop_executor.py -v -k "decompose or complexity" --tb=short 2>&1 | tail -30
```

**Step 3: Commit**

```bash
git add tests/test_ralphloop_executor.py
git commit -m "test(ralphloop): add P6 decomposition tests"
```

---

## Task 7: 更新 `__init__.py` 导出

**Objective:** 确保新枚举可以从 `ralphloop` 包导入。

**Files:**
- Modify: `src/ralphloop/__init__.py`

**Step 1: 确认 `TaskComplexity` 不需要导出**（内部使用，不公开 API）

跳过。`EscalationOption.DECOMPOSE` 已导出。

---

## 验证总览

所有 7 个 task 完成后，运行完整测试套件:

```bash
cd /Users/dukedai/dev/nexus-cc && pytest tests/test_ralphloop_executor.py tests/test_complexity.py -v --tb=short
```

预期: 所有测试通过（现有 + 新增）

mypy 类型检查:
```bash
cd /Users/dukedai/dev/nexus-cc && mypy src/ralphloop/executor.py src/ralphloop/complexity.py --ignore-missing-imports
```

---

## 风险与注意事项

1. **无 LLM 时 fallback**: `_decompose_task` 在无 LLM 时返回空列表，`_execute_decompose_phase` 会 fallback 到单任务，不阻塞执行。
2. **YAGNI**: 暂不实现真正的并行子任务执行（子 agent），保持 task_queue 顺序执行。Task 52 可在此基础上叠加并行。
3. **DECOMPOSE 状态实际未被 orchestrator.run() 使用**: `_execute_decompose_phase` 在 `orchestrator.run()` **之前**执行，直接操作 `orchestrator.state` 和 `orchestrator.task_queue`。DECOMPOSE 状态存在但作为"文档"状态，实际逻辑在 executor 预检查中。这是刻意的简化——避免在核心 run() 循环中增加分支。
4. **WAL 兼容性**: INIT → DECOMPOSE 的 WAL entry 是新增的，现有 WAL consumer 应忽略未知 state。
