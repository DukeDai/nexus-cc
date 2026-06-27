# Nexus Plan-First Redesign — Design Spec

> **Date**: 2026-06-27
> **Status**: Draft for review
> **Scope**: Nexus v1 重构方案
> **Goal**: 让 Nexus 成为真正可用的 Claude Code 替代品，核心差异化是 **plan-first + plan-review TUI**。

---

## 1. 背景与动机

### 1.1 当前状态（2026-06-27 审计）

| 维度 | 数据 |
|---|---|
| 代码规模 | 119 文件 / ~36k LOC |
| src 模块数 | 14 个 |
| benchmark | 77/77 passed (结构性测试，mock-based) |
| pytest | 55/55 passed |
| Roadmap P0-P7 | 5/8 claimed done（其余 3 待做）|
| 真实端到端任务成功率 | 未量化（benchmark 用 MockLLM）|

**核心问题**：
1. TUI 与运行循环的契约是 callback 拼凑，用户命令（approve/reject/retry/skip）只 append 消息，不真正影响 walker
2. 8-state machine (PLAN/ACT/VERIFY/REFLECT) 复杂但与 LLM 实际工作流脱节
3. Subagent 并行、TDD 强制、SelfEvolution 等 8 大特性每个只做到 70%，没有 killer feature
4. 真实任务端到端验证缺失

### 1.2 v1 目标：Claude Code 替代品

**用户使用方式**：90% 时间在交互式 TUI，10% CLI 单任务。
**核心体验**：
- 提交任务 → 看到结构化 Plan → 可编辑 → 批准 → walker 逐步执行
- 任何 step 完成后可暂停、编辑后续步骤、恢复
- 危险命令需要用户确认
- ASK_USER 步骤弹模态提问
- 崩溃后可从 WAL 恢复未完成 plan

**明确不做（v1 范围外）**：subagent 并行、TDD 自动强制、self-evolution、MCP server 接入、sub-plan 嵌套分解。

---

## 2. 架构总览

### 2.1 核心理念

**Plan 是真理来源**。AgentRuntime 是 `PlanWalker`，TUI 是 Plan 的编辑器和观察器。

```
┌─────────────────────────────────────────────────────────────┐
│  TUI (Textual)                                              │
│  ├─ Plan Review Panel   (结构化 Plan，可编辑 step)          │
│  ├─ Execution Panel     (step-by-step 执行进度)            │
│  ├─ Tool Output Panel   (每个 tool 的输入/输出)             │
│  └─ Command Palette     (approve/edit/retry/abort/resume)  │
└──────────────────┬──────────────────────────────────────────┘
                   │ ControlChannel (双向 typed events)
                   ↓
┌─────────────────────────────────────────────────────────────┐
│  AgentRuntime (单进程 asyncio)                              │
│  ├─ Planner        : task → Plan (LLM, structured output)   │
│  ├─ PlanWalker     : 遍历 Plan.steps[]，逐个执行            │
│  │   ├─ PlanStep(kind=TOOL, ...)      → ToolRegistry.execute│
│  │   ├─ PlanStep(kind=VERIFY, ...)    → VerificationPipeline│
│  │   ├─ PlanStep(kind=CRITIQUE, ...)  → LLM self-review     │
│  │   └─ PlanStep(kind=ASK_USER, ...)  → 阻塞等用户输入      │
│  ├─ ControlChannel : pause/resume/abort/edit_step 事件      │
│  └─ WAL : Plan + cursor(checkpoint) 持久化                  │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────┐
│  Tool Layer (8 个核心 tool)                                 │
│  Read / Write / Edit / Bash / Glob / Grep / Git / WebSearch │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 关键简化

| 现有模块 | 处置 | 理由 |
|---|---|---|
| `RalphLoop` 8-state machine | 删除 | 退化为 PlanStep.kind 枚举 |
| `SubagentIntegration` | 删除 | v1 单 agent |
| `TDDEnforcer` | 删除 | 退化为可选 VERIFY step |
| `SelfEvolutionEngine` | 整目录删除 | v2 再考虑 |
| `WorkingBuffer` | 删除 | 简化为 git worktree 可选 |
| `VerificationPipeline` | 保留为 runner | 作为 VERIFY step 执行器 |
| `ToolRegistry` | 保留 | 真正的 tool 列表 |
| `CheckpointManager` + `WALManager` | 保留重写 | step-level checkpoint |
| `agent_loop.py` | 重写为 walker | plan-driven 而不是 streaming-driven |
| `tui/*` | 整目录重写 | 从 Rich Live 迁到 Textual |

---

## 3. Plan 数据结构

### 3.1 Plan 数据模型

```python
# src/agent/plan.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Any

@dataclass
class PlanStep:
    id: str                          # uuid，便于 TUI 引用
    kind: Literal["TOOL", "VERIFY", "CRITIQUE", "ASK_USER"]
    intent: str                      # 人类可读：「读取 models.py 找到 User 类」
    tool: str | None = None          # kind=TOOL 时：Read/Edit/Bash/...
    args: dict[str, Any] = field(default_factory=dict)
    success_criteria: str = ""       # 「返回非空用户列表」「pytest 全过」
    on_failure: Literal["abort", "retry", "skip", "ask"] = "ask"
    timeout_s: int = 120

@dataclass
class Plan:
    plan_id: str
    spec: str                        # 原始任务描述
    steps: list[PlanStep]
    assumptions: list[str]
    risks: list[str]
    created_at: datetime
    version: int = 1                 # 编辑后自增，TUI 标识脏

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d): ...
    def find_step(self, step_id) -> PlanStep | None: ...
```

### 3.2 PlanStep.kind 语义

| kind | 含义 | 执行器 |
|---|---|---|
| `TOOL` | 调一个 tool | `ToolRegistry.execute(tool, args)` |
| `VERIFY` | 跑 verification gate | `VerificationPipeline.run()` |
| `CRITIQUE` | LLM 自检本步结果 | 短 prompt 让 LLM 评判「这步真的完成了吗」 |
| `ASK_USER` | 阻塞等用户回答 | 弹 TUI 提问，存到 `args["answer"]` |

---

## 4. AgentRuntime 接口

### 4.1 Public API

```python
# src/agent/runtime.py
class AgentRuntime:
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        verification: VerificationPipeline,
        wal: WALManager,
        channel: ControlChannel,        # TUI ↔ Runtime 双向通道
    ): ...

    # ─── 阶段 1: 生成 Plan ───
    async def plan(self, task: str, *, spec: str | None = None) -> Plan:
        """LLM 生成结构化 Plan，retry 直到解析成功。"""

    # ─── 阶段 2: 走 Plan（核心循环）───
    async def walk(self, plan: Plan) -> PlanResult:
        """遍历 plan.steps[]，每步通过 self._channel.emit(WalkEvent) 发事件。
        TUI 订阅 ControlChannel._events 队列消费事件。"""

    # ─── 控制通道（来自 TUI/外部）───
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def abort(self, reason: str = "") -> None: ...
    def edit_step(self, step_id: str, new_step: PlanStep) -> None: ...
    def insert_step(self, after_id: str, new_step: PlanStep) -> None: ...
    def remove_step(self, step_id: str) -> None: ...
    def reorder_steps(self, ordered_ids: list[str]) -> None: ...
    def answer_question(self, step_id: str, answer: str) -> None: ...
```

### 4.2 WalkEvent 类型

```python
# src/agent/events.py
class WalkEvent:
    pass

@dataclass
class PlanStarted(WalkEvent): plan: Plan
@dataclass
class StepStarted(WalkEvent): step: PlanStep; index: int; total: int
@dataclass
class ToolCallStarted(WalkEvent): tool: str; args: dict; step_id: str
@dataclass
class ToolCallCompleted(WalkEvent): result: ToolResult; step_id: str
@dataclass
class StepCompleted(WalkEvent): step: PlanStep; result: StepResult
@dataclass
class StepFailed(WalkEvent): step: PlanStep; error: str
@dataclass
class AskUser(WalkEvent): step: PlanStep; question: str; options: list[str]
@dataclass
class Paused(WalkEvent): step_id: str | None
@dataclass
class Resumed(WalkEvent): ...
@dataclass
class Aborted(WalkEvent): reason: str
@dataclass
class PlanCompleted(WalkEvent): results: list[StepResult]
```

### 4.3 ControlChannel 内部实现

```python
# src/agent/control.py
class ControlChannel:
    """双向事件通道，连接 TUI 与 Runtime，同一 asyncio event loop。"""
    _commands: asyncio.Queue[Command]
    _events: asyncio.Queue[WalkEvent]
    _pause_event: asyncio.Event

    async def send_command(self, cmd: Command) -> None: ...
    async def recv_event(self) -> WalkEvent: ...
    async def wait_if_paused(self) -> None: ...
```

**关键设计**：walker 是单 asyncio 任务，`pause()` 清 `_pause_event`，每 step 开始前 await；`abort()` 抛 `PlanAborted`。TUI 是另一 asyncio 任务，消费 events 队列、发 commands。两者同 event loop，无锁。

---

## 5. TUI 设计

### 5.1 技术选型：Textual

**从 Rich Live 迁到 Textual**。理由：
- 原生 `asyncio` 集成（与 AgentRuntime 同 event loop，无线程、无锁）
- `Tree` widget 完美匹配 Plan 结构
- `ModalScreen` 处理 ASK_USER / 危险命令确认
- 内置 command palette (`ctrl+\`)
- Mouse + keyboard 双支持

依赖变化：`textual>=0.50` 加入 `pyproject.toml`。

### 5.2 屏幕分区

```
┌─ Header ────────────────────────────────────────┐
│ Nexus │ Task: "Add email field to User model"  │
│ State: walking │ Step 2/4 │ Context: 32% GOOD   │
├─ Plan Panel (left, 40%) ─────────────────────────┤
│ ▼ Plan (4 steps, v1)                            │
│   ✓ 1. [TOOL] Read src/models.py                │
│     intent: "find User class definition"        │
│   ▶ 2. [TOOL] Edit models.py +email field       │
│     intent: "add email field to User class"     │
│     args: { path: "src/models.py", ... }         │
│   ○ 3. [VERIFY] Run pytest tests/test_models.py │
│   ○ 4. [ASK_USER] "Which migration strategy?"   │
├─ Execution Panel (right top, 60%) ───────────────┤
│ Step 2/4: Edit models.py                        │
│ ─ tool_call edit_file                           │
│   args: { path: "src/models.py", old: "..."}    │
│   ✓ success (12 inserted, 3 deleted)            │
│ ─ checkpoint WAL cursor=step_2                  │
├─ Tool Output (right bottom, 60%) ───────────────┤
│ [streaming LLM output of next step...]          │
├─ Footer ─────────────────────────────────────────┤
│ [a]approve [e]edit [d]del [j/k]nav [J/K]reorder  │
│ [p]pause [x]abort [?]help [q]quit               │
└─────────────────────────────────────────────────┘
```

### 5.3 PlanStep Edit Modal

按 `e` 触发：

```
┌─ Edit Step 2 ──────────────────────────┐
│ Intent:                                 │
│ ┌─────────────────────────────────────┐ │
│ │ add email field to User class       │ │
│ └─────────────────────────────────────┘ │
│ Tool: [Edit ▼]                          │
│ Args (JSON):                            │
│ ┌─────────────────────────────────────┐ │
│ │ { "path": "src/models.py",          │ │
│ │   "old_string": "name: str",        │ │
│ │   "new_string": "name: str\nemail:  │ │
│ │   str" }                            │ │
│ └─────────────────────────────────────┘ │
│ Success criteria:                       │
│ ┌─────────────────────────────────────┐ │
│ │ pytest tests/test_models.py passes  │ │
│ └─────────────────────────────────────┘ │
│ On failure: [ask ▼]  Timeout: [120]s    │
│                              [Cancel] [Save] │
└─────────────────────────────────────────┘
```

### 5.4 关键交互流程

**流程 A: 提交任务 → Plan Review**
1. 用户按 `:` 进入 command palette
2. 输入 `new <task description>` → 提交
3. Runtime.plan(task) 调用 LLM 生成结构化 Plan（retry 直到 JSON 解析）
4. PlanStarted event → TUI 渲染 Plan Panel
5. 用户编辑（`e` 进 Modal），满意后按 `a`
6. APPROVE_PLAN 命令 → Runtime.walk(plan) 开始

**流程 B: 执行中 → 暂停修改**
1. Runtime.walk() 执行 step 3 中
2. StepStarted event → Execution Panel 更新
3. 危险工具（Bash rm -rf）→ ModalScreen 弹确认
4. 用户按 `p` → PAUSE 命令 → walker 在下一步前 await pause_event
5. TUI Paused event → Plan Panel 解锁
6. 用户编辑 step 4 → RESUME → walker 继续（plan.version+1）

**流程 C: ASK_USER step**
1. walker 遇到 PlanStep(kind=ASK_USER)
2. emit AskUser event
3. TUI 弹 ModalScreen 显示问题 + 选项
4. 用户选择 → ANSWER_QUESTION 命令携带 answer
5. walker 恢复，answer 写入 args["answer"]

**流程 D: 崩溃恢复**
1. Runtime 周期性把 (plan, cursor) 写入 WAL
2. 进程崩溃后重启 → WALManager.recover() 读最近 (plan, cursor)
3. TUI 启动时检测未完成 plan → 弹 ModalScreen 询问"恢复？丢弃？存为模板？"

### 5.5 键盘绑定

| 按键 | 上下文 | 动作 |
|---|---|---|
| `:` | 任意 | Command palette |
| `a` | Plan Panel | APPROVE_PLAN |
| `r` | Plan Panel | REJECT_PLAN |
| `e` | step selected | 进入 Step Edit Modal |
| `d` | step selected | 删除 step |
| `i` | step selected | 在其后插入新 step |
| `j` / `k` | 任意 | 下/上移动 |
| `J` / `K` | step selected | 下/上重排序 |
| `p` | 执行中 | PAUSE |
| `P` | 暂停中 | RESUME |
| `x` | 任意 | ABORT（弹确认）|
| `?` | 任意 | 帮助屏 |
| `q` | 任意 | QUIT（运行中弹确认）|

---

## 6. 错误处理 / 中断 / 恢复

### 6.1 错误分类

| 错误源 | 例子 | 默认处理 |
|---|---|---|
| Tool 执行失败 | read_file 文件不存在、Bash 退出码非 0 | 走 step.on_failure 策略 |
| LLM 调用失败 | rate limit、network timeout | 自动 retry (指数退避，max 3)，降级小模型 |
| Plan 解析失败 | LLM 返回非法 JSON | 重试 + 更强 prompt，max 3 次后 fail |
| Plan 步骤语义错 | LLM 生成的 step 引用不存在的 tool | 走 step.on_failure=ask |
| 用户主动中断 | REJECT_PLAN / ABORT / Ctrl-C | 立即停止，保留已完成的 step 结果 |
| 进程崩溃 | SIGKILL / OOM | 从 WAL checkpoint 恢复 |

### 6.2 Step 失败处理流水线

```python
# src/agent/walker.py
async def execute_step(self, step: PlanStep) -> StepResult:
    for attempt in range(1 + MAX_RETRIES_PER_STEP):  # 默认 MAX=2
        try:
            result = await self._execute_step_once(step)
            self._wal.checkpoint(plan=self._plan, cursor=step.id, result=result)
            return result
        except StepFailure as e:
            if attempt < MAX_RETRIES_PER_STEP:
                continue
            return await self._handle_step_failure(step, e)

async def _handle_step_failure(self, step: PlanStep, error: StepFailure) -> StepResult:
    match step.on_failure:
        case "abort":
            raise PlanAborted(f"step {step.id} failed: {error}")
        case "retry":
            return await self.execute_step(step)
        case "skip":
            return StepResult(status="skipped", error=str(error))
        case "ask":
            await self._channel.emit(AskUser(...))
            answer = await self._channel.wait_for_answer(step.id)
            # answer 转化为新 on_failure 决策
            ...
```

### 6.3 Pause 语义：只在 step 边界

**关键决策**：`pause()` **不能**中断正在执行的 tool call。理由：
- `edit_file` 写到一半被中断 → 文件状态不一致
- `bash` 长命令被中断 → 副作用不可知
- checkpoint 只能在 step 完成的"已知状态"上做

```python
async def walk(self, plan: Plan) -> PlanResult:
    for idx, step in enumerate(plan.steps):
        await self._channel.wait_if_paused()    # pause_event.clear() 时 await
        if self._aborted:
            raise PlanAborted(self._abort_reason)
        await self._channel.emit(StepStarted(step=step, index=idx, total=len(plan.steps)))
        try:
            result = await self.execute_step(step)
            await self._channel.emit(StepCompleted(step=step, result=result))
        except PlanAborted:
            await self._channel.emit(Aborted(reason=...))
            raise
```

**UX 含义**：用户按 `p` 后看到"Pausing after step X..."提示，walker 完成当前 step 后停在下一步前。**不是瞬时暂停**。

### 6.4 Resume 语义：WAL + plan.version

```python
# WAL 格式（JSONL）
{"tx": "checkpoint", "plan_id": "...", "cursor": "step_3", "step_result": {...}, "ts": ...}
{"tx": "plan_edit", "plan_id": "...", "version": 2, "new_plan": {...}, "ts": ...}

# WALManager.recover()
async def recover(self) -> tuple[Plan, str] | None:
    last = self._find_last_unfinished()
    if not last:
        return None
    return (last.plan, last.cursor)

# Runtime.resume()
async def resume(self, plan: Plan, from_step_id: str) -> PlanResult:
    completed_ids = self._wal.get_completed_step_ids(plan.plan_id)
    remaining = [s for s in plan.steps if s.id not in completed_ids]
    return await self.walk(plan.with_steps(remaining), from_step_id)
```

**关键**：用户编辑 plan 后 version 自增。resume 时如果 WAL version < 当前 version，提示"plan 已修改，从原 cursor 还是从新 plan 开始？"。

### 6.5 启动时的 crash 恢复 UI

```python
class RecoverModal(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes", "Resume"), Binding("n", "no", "Discard")]
    def compose(self) -> ComposeResult:
        yield Static(f"Found unfinished plan: {plan.spec}")
        yield Static(f"Completed: {len(completed)}/{len(plan.steps)} steps")
        yield Static("[y] Resume  [n] Discard")
```

### 6.6 错误 UX 规则

- **Tool 失败**：Execution Panel 标红，显示 tool name + 错误摘要，附 "view full output" 链接
- **LLM 失败**：显示 retry attempt N/3 + 退避倒计时
- **Plan 解析失败**：自动重试（不可见），仅在放弃时显示
- **所有错误**写入 `PlanResult.error_log`，TUI 可用 `:errors` 命令查看历史

---

## 7. 测试策略

### 7.1 测试金字塔

**Level 1：纯单元测试**
- `Plan.to_dict/from_dict` round-trip
- `PlanStep` 校验（tool 存在性、args 类型）
- `ControlChannel` send/recv
- 各种 `on_failure` 策略的状态机
- WAL checkpoint 序列化和恢复

**Level 2：Mock LLM 集成测试**
- `Runtime.plan()` 用 mock LLM 返回固定 JSON，验证 Plan 解析
- `Runtime.walk()` 用 mock ToolRegistry，验证 step 顺序执行
- Pause/resume 流程：mock 100ms step，触发 pause，验证 walker 在下一 step 前停
- Crash recovery：写 WAL → 模拟崩溃 → 重启 → 验证从 cursor 恢复

**Level 3：真实 LLM 烟雾测试（CI 跳过，开发者手动）**
- 用真 Anthropic API 跑 3 个固定任务：
  1. "在 src/foo.py 加一行注释"
  2. "重构 tests/ 文件名为 snake_case"
  3. "运行 pytest 并修复失败的测试"

**Level 4：TUI 交互测试（用 Textual Pilot）**
```python
async def test_plan_review_approve():
    app = NexusApp()
    async with app.run_test() as pilot:
        await pilot.press(":")
        await pilot.type("new add type hints")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.runtime.state == "completed"
```

### 7.2 验收标准（Definition of Done）

**v1 必须满足**（MVP）：
- [ ] 用户在 TUI 中提交任务，3 秒内看到结构化 Plan
- [ ] Plan 每个 step 可编辑（intent/tool/args/criteria/on_failure/timeout）
- [ ] 用户按 `a` 后 walker 开始执行
- [ ] 执行中可按 `p` 暂停（在下一步前停下），可编辑 plan 后恢复
- [ ] 任何 Bash 命令在执行前显示在 TUI，危险命令需用户确认
- [ ] `ASK_USER` step 弹出 Modal，用户选择后 walker 继续
- [ ] 进程崩溃后重启能恢复未完成 plan
- [ ] 至少 8 个核心 tool：Read/Write/Edit/Bash/Glob/Grep/Git/WebSearch
- [ ] pytest 单元 + 集成测试 ≥ 50 个，全部通过
- [ ] 真实 LLM 烟雾测试 3 个任务全部成功
- [ ] TUI Pilot 测试覆盖核心交互路径

**v1 不做**：
- ❌ 多 agent 并行
- ❌ TDD 自动强制
- ❌ Self-evolution / 跨会话学习
- ❌ MCP server 集成（v2）
- ❌ Sub-Plan（plan 嵌套分解）

---

## 8. 实现里程碑

### Week 1：核心 runtime
- Day 1-2：`src/agent/plan.py` + `src/agent/events.py`
- Day 3-4：`src/agent/control.py` + `src/agent/walker.py`（不含 LLM）
- Day 5：`src/agent/runtime.py` + LLM 集成（mock + 真实）
- Day 6-7：单元测试 + Mock LLM 集成测试

### Week 2：TUI 重写
- Day 1-2：Textual app skeleton + 屏幕分区
- Day 3：Plan Panel + Step Edit Modal
- Day 4：Execution Panel + Tool Output Panel
- Day 5-6：Command palette + 键盘绑定 + 帮助屏
- Day 7：Pilot 测试覆盖核心路径

### Week 3：错误处理 + WAL
- Day 1-2：WAL 重写为 step-level checkpoint
- Day 3-4：on_failure 策略 + retry 流水线
- Day 5：pause/resume 真实测试
- Day 6-7：crash recovery UI + 测试

### Week 4：真实 LLM 端到端 + 抛光
- Day 1-2：8 个核心 tool 实现
- Day 3-4：3 个真实烟雾测试 + 修复
- Day 5-6：错误 UX 抛光 + 文档
- Day 7：发布 v1

---

## 9. 代码删除清单

```
src/ralphloop/
├── states.py            # 删除：8-state 枚举
├── transitions.py       # 删除：转换表
├── orchestrator.py      # 删除：RalphLoop class
├── subagent_registry.py # 删除
├── subagent_integration.py  # 删除
├── tdd_enforcer.py      # 删除
├── executor.py          # 保留并重命名：AgentRuntime
├── agent_loop.py        # 重写为 walker
└── implementation_context.py  # 重写或删除

src/tui/
├── app.py               # 重写为 Textual app
├── nexus_tui.py         # 删除
├── input_handler.py     # 删除（用 Textual binding）
├── approval.py          # 删除（用 ModalScreen）
├── state_view.py        # 重写为 Plan Panel
├── agent_view.py        # 重写为 Execution Panel
├── context_view.py      # 简化为 header 的一部分
└── task_view.py         # 重写为 Plan 列表

src/self_evolution/      # 整目录删除（v2 再考虑）
src/hooks/               # 简化为 pre/post-tool hook（仅 dangerous 检测）
src/verification/        # 保留 security_scan.py，作为 VERIFY step 的 runner
src/mcp/                 # 整目录保留但 v1 不接入
```

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Textual 学习曲线 | TUI 重写延期 | 第 1 周做 spike：跑通 hello world + Tree + Modal |
| LLM 不返回合法 JSON | plan 永远失败 | 强 prompt + 多次 retry + markdown 代码块 fallback |
| WAL 损坏 | 恢复失败 | append-only + checksum；损坏时 Modal 让用户选择丢弃 |
| Pause 不能中断 tool | UX 差 | TUI 明确显示 "Pausing after step X..." |
| 删除太多代码丢失好想法 | 后悔 | 每个删除前 git tag；保留 `legacy/` 目录 1 个月 |

---

## 11. 开放问题

1. **Plan 生成的最大 token 数**：复杂任务 LLM 可能生成 50+ step 的 plan，是否需要 max_steps 限制？
2. **多用户并发**：v1 单进程单 plan，是否需要 session manager 支持多 plan 并行？
3. **Tool 参数的 JSON Schema 校验**：TUI 编辑 args 时是否实时校验？还是只在 execute 时报错？
4. **Plan 的版本控制**：plan.version 够用还是需要完整 diff？
5. **VERIFY step 的粒度**：是单步 gate 还是 multi-gate（pytest + mypy + security）？

这些问题将在 writing-plans 阶段细化。