# Nexus Architecture — RalphLoop 核心架构

> **版本**：v7（代码同步版）
> **最后更新**：2026-05-10
> **目标**：超越 Claude Code/Codex/OpenCode 的下一代 coding AI agent
> **诚实状态**：所有 6 层 wired 并已验证；benchmark 77/77 通过；55/55 tests 通过；speculative next-task PLAN 预计算已实现；mypy 15→0 errors。

---

## 1. 顶层执行流

```
CLI (src/cli/main.py)
  └─ run command (src/cli/commands/run.py)
       └─ RalphLoopExecutor.run_task()        [入口]
            ├─ WALManager.log_transition()     [状态转换记录]
            ├─ RalphLoop.run()                [状态机驱动]
            │    ├─ _execute_state() → 根据 self.state 分派到对应 phase
            │    │    ├─ DECOMPOSE → _execute_decompose_phase()
            │    │    ├─ PLAN     → _execute_plan() → run_agent_loop()
            │    │    ├─ ACT      → _execute_act() → SubagentIntegration (并行)
            │    │    ├─ VERIFY   → _execute_verify() → verification pipeline
            │    │    └─ REFLECT  → _execute_reflect() → SelfEvolutionEngine
            │    ├─ _transition()             [根据 trigger + context 查转换规则]
            │    ├─ periodic checkpoint        [每 CHECKPOINT_INTERVAL 次迭代]
            │    └─ context tier 警告检测
            ├─ CheckpointManager.save()       [周期快照 + 最终快照]
            └─ SelfEvolutionEngine.on_task_done() [跨session学习]
```

**状态机状态**（`RalphState` in `src/ralphloop/states.py`）：

```
DECOMPOSE → PLAN → ACT → VERIFY → REFLECT → (NEXT_TASK | COMMIT)
                                                       ↓
                                                  ESCALATE → (RETRY | ABANDON | ABORT)
```

转换由 `get_valid_transitions()` 和 `get_abort_transition()` 在 `transitions.py` 中控制。

---

## 2. RalphLoopExecutor（6层统一入口）

**文件**：`src/ralphloop/executor.py`（`RalphLoopExecutor` 类）

| 层 | 组件 | 文件 | 初始化参数 |
|----|------|------|-----------|
| 1 | WALManager | `src/context/wal.py` | `enable_wal=True` |
| 2 | CheckpointManager | `src/context/checkpoint.py` | `enable_checkpoint=True` |
| 3 | SelfEvolutionEngine | `src/self_evolution/engine.py` | `enable_self_evolution=True` |
| 4 | ModelRouter | `src/llm/model_router.py` | `enable_model_router=True` |
| 5 | SubagentIntegration | `src/ralphloop/subagent_integration.py` | `enable_parallel_subagents=True` |
| 6 | TDDEnforcer + VerificationPipeline | `src/ralphloop/tdd_enforcer.py`, `src/verification/pipeline.py` | `enable_tdd=True`, `enable_verification_pipeline=True` |

**WAL 实例传递**：每个 `run_agent_loop` 调用通过 `wal=self._wal` 参数传入，保证崩溃恢复可重放完整日志。

**Streaming**：executor 接受 `streaming_callback: Callable[[str], None] | None = None` 参数，透传给 PLAN 和 ACT phase 的 `run_agent_loop` 调用。

---

## 3. 核心组件详解

### 3.1 状态机 & Agent Loop

| 文件 | 职责 |
|------|------|
| `src/ralphloop/states.py` | `RalphState` enum: DECOMPOSE / PLAN / ACT / VERIFY / REFLECT / ESCALATE / COMMIT / ABORT |
| `src/ralphloop/transitions.py` | 转换规则（`get_valid_transitions`, `get_abort_transition`, `TransitionContext`）和 `TransitionTrigger` enum |
| `src/ralphloop/orchestrator.py` | `RalphLoop` — 状态机循环，`run()` 方法驱动整个流程 |
| `src/ralphloop/agent_loop.py` | `run_agent_loop()` — 单次 LLM+工具闭环，支持 streaming callback |

**`RalphLoop.run()` 返回值**：
```python
{
    "success": bool,              # True iff final_state == COMMIT
    "outcome": str,               # "full" | "partial" | "aborted" | "early"
    "tasks_done": int,
    "tasks_total": int,
    "final_state": RalphState,
    "metrics": RalphLoopMetrics,
    "checkpoint_path": str | None,
    "error_log": list[str],
}
```

### 3.2 LLM 层

| 文件 | 职责 |
|------|------|
| `src/llm/client.py` | `LLMClient` — `complete()` / `complete_streaming()` 两种调用模式 |
| `src/llm/model_router.py` | `ModelRouter` — 根据 `TaskType`（CODE/ANALYSIS/REASONING/FAST）选择模型；支持 cost 估算 |

### 3.3 Subagent 系统

| 文件 | 职责 |
|------|------|
| `src/ralphloop/subagent_registry.py` | 5种 Agent 注册（Specifier/Implementer/Reviewer/Security/SCAFFOLD） |
| `src/ralphloop/subagent_integration.py` | `run_implementer_with_review()` — `ThreadPoolExecutor(max_workers=2)` 并行执行 implementer + reviewer；`run_security_scan()` 串行安全扫描 |

### 3.4 Context 层（WAL / Checkpoint / 监控）

| 文件 | 职责 |
|------|------|
| `src/context/wal.py` | `WALManager` — SQLite WAL 模式，崩溃后 `recover()` 生成恢复计划 |
| `src/context/checkpoint.py` | `CheckpointManager` — 完整状态快照，UUID 标识 |
| `src/context/monitor.py` | `ContextMonitor` — 4-tier 上下文预算（PEAK/GOOD/DEGRADING/POOR） |
| `src/context/claudemd.py` | CLAUDE.md 三层合并（全局/项目/目录） |
| `src/context/working_buffer.py` | 工作缓冲区 — 隔离的代码实验沙盒，`apply_buffer()` 正式提交 |
| `src/context/worktree.py` | Git worktree 管理 |

### 3.5 验证管道（VerificationPipeline）

| 文件 | 职责 |
|------|------|
| `src/verification/pipeline.py` | `VerificationPipeline.run()` — 顺序执行所有 gate |
| `src/verification/security_scan.py` | 安全扫描（hardcoded secret 检测） |
| `src/verification/test_gate.py` | pytest 自动发现并运行 |
| `src/verification/tdd_gate.py` | TDD 测试存在性检查 |
| `src/verification/review_gate.py` | 代码审查 gate |

VerificationPipeline 在 ACT phase 内联执行（`_execute_act_single` 返回值含 `pipeline_warnings`），由 `enable_verification_pipeline=True` Toggle。

### 3.6 自进化引擎

| 文件 | 职责 |
|------|------|
| `src/self_evolution/engine.py` | `SelfEvolutionEngine` — 错误模式捕获 → 技能库；`get_best_recovery()` 跨 session 恢复 |
| `src/skills/capture.py` | 技能捕获 |
| `src/skills/author.py` | 技能创作 |
| `src/skills/loader.py` | 技能加载 |

### 3.7 工具层

| 文件 | 工具 |
|------|------|
| `src/tools/bash.py` | `BashTool` |
| `src/tools/edit.py` | `EditTool` |
| `src/tools/write.py` | `WriteTool` |
| `src/tools/read.py` | `ReadTool` |
| `src/tools/glob.py` | `GlobTool` |
| `src/tools/grep.py` | `GrepTool` |
| `src/tools/git.py` | `GitTool` |
| `src/tools/web_search.py` | `WebSearchTool` |
| `src/engine/registry.py` | `ToolRegistry` — 动态工具发现，`register_all(package_name="nexus.tools")` |
| `src/engine/executor.py` | `ToolExecutor` — 工具执行 + `HookManager` |

### 3.8 Hook 系统

| 文件 | 职责 |
|------|------|
| `src/hooks/hook_manager.py` | `HookManager` |
| `src/hooks/pre_tool_hook.py` | 前置钩子 |
| `src/hooks/post_tool_hook.py` | 后置钩子 |
| `src/hooks/integration.py` | 钩子集成点 |

### 3.9 MCP 集成

| 文件 | 职责 |
|------|------|
| `src/mcp/connection.py` | `MCPConnectionManager` — `_connect_stdio()` 真实连接（非模拟） |
| `src/mcp/integration.py` | `RalphLoopMCPBridge` — `plan_with_mcp()` / `verify_with_mcp()` |
| `src/mcp/client.py` | MCP client |
| `src/mcp/config.py` | MCP 配置 |

### 3.10 Session 管理

| 文件 | 职责 |
|------|------|
| `src/session/manager.py` | `SessionManager` |
| `src/session/store.py` | `SessionStore` |
| `src/session/models.py` | `Session` / `SessionSummary` 数据模型 |

### 3.11 TUI

| 文件 | 职责 |
|------|------|
| `src/tui/app.py` | TUI App 基类 |
| `src/tui/nexus_tui.py` | `NexusTUI` — Rich TUI 主应用 |
| `src/tui/state_view.py` | 状态视图 |
| `src/tui/agent_view.py` | Agent 视图 |
| `src/tui/context_view.py` | 上下文视图 |
| `src/tui/task_view.py` | 任务视图 |
| `src/tui/approval.py` | 审批视图 |

### 3.12 CLI

| 文件 | 职责 |
|------|------|
| `src/cli/main.py` | Click 根命令 |
| `src/cli/commands/run.py` | `run` 命令 → `RalphLoopExecutor.run_task()` |
| `src/cli/commands/tui.py` | `tui` 命令 |
| `src/cli/commands/session.py` | `session` 命令（list/resume） |
| `src/cli/commands/mcp.py` | `mcp` 命令 |
| `src/cli/commands/skills.py` | `skills` 命令 |
| `src/cli/commands/cost.py` | `cost` 命令 |

---

## 4. 目录结构

```
src/
├── agents/          # Subagent 实现（Specifier/Implementer/Reviewer/Security）
├── cli/
│   └── commands/    # run / tui / session / mcp / skills / cost
├── context/         # WAL / Checkpoint / Monitor / CLAUDE.md / WorkingBuffer
├── engine/          # ToolRegistry / ToolExecutor / HookManager
├── hooks/           # pre/post 工具钩子
├── llm/             # LLMClient / ModelRouter
├── mcp/             # MCP bridge / client / connection / integration
├── ralphloop/       # 核心状态机 + executor
│   ├── executor.py         # RalphLoopExecutor（入口，6层组件初始化）
│   ├── orchestrator.py     # RalphLoop（状态机循环，run() 方法）
│   ├── agent_loop.py       # run_agent_loop() — LLM+工具闭环
│   ├── subagent_integration.py  # 并行 subagent（ThreadPoolExecutor）
│   ├── subagent_registry.py     # Agent 注册表
│   ├── tdd_enforcer.py     # TDD 强制（RED→GREEN→REFACTOR）
│   ├── states.py            # RalphState enum
│   ├── transitions.py       # 转换规则 + TransitionTrigger
│   └── implementation_context.py
├── self_evolution/  # 自进化引擎 + 技能库
├── session/         # Session 持久化
├── skills/          # 技能捕获/创作/加载
├── tools/           # 工具实现
├── tui/             # Rich TUI
└── verification/     # VerificationPipeline（security / pytest / tdd / review gates）
```

---

## 5. 使用方法

```python
from ralphloop.executor import RalphLoopExecutor

executor = RalphLoopExecutor(
    workdir="./project",
    enable_wal=True,
    enable_checkpoint=True,
    enable_self_evolution=True,
    enable_model_router=True,
    enable_parallel_subagents=True,
    enable_tdd=True,
    streaming_callback=lambda chunk: print(chunk, end="", flush=True),
)

result = executor.run_task("Create a REST API with FastAPI")
# result = {"success": bool, "outcome": str, "tasks_done": int, "tasks_total": int, ...}
```

---

## 6. 关键设计决策

### 6.1 WAL → Agent Loop Wiring
每个 `_execute_plan` / `_execute_act_single` 调用 `run_agent_loop` 时传入 `wal=self._wal`。崩溃恢复时 WAL 日志可被回放，生成恢复计划。

### 6.2 Subagent 并行
`_execute_act()` 调用 `SubagentIntegration.run_implementer_with_review()`，内部使用 `ThreadPoolExecutor(max_workers=2)` 并行运行 implementer + reviewer。Security scan 在 Reviewer 完成后串行执行。

### 6.3 ModelRouter 在 Subagent 级别
每个 subagent 启动时通过 `_get_llm_client` 查询 `ModelRouter`，根据任务类型获取对应 LLM client。

### 6.4 TDD 是 Prompt-Based + Verification Gate
TDDEnforcer 通过在 system prompt 里注入 TDD 指令，`tdd_gate.py` 检查测试文件存在性。`enable_tdd=True` Toggle。

### 6.5 Checkpoint Periodic Guard Bug Fix
`_checkpoint_count` 在 `_checkpoint()` 内部递增。`run()` 的 periodic checkpoint 条件使用 `>= 1`（而非 `> 0`），确保首次 checkpoint 后才能触发后续 periodic checkpoint。

### 6.6 Speculative Next-Task PLAN
`run_tasks([A, B, C])` 创建单个 `RalphLoop` orchestrator 处理所有任务。在 ACT 阶段，`_speculative_start()` 启动 `ThreadPoolExecutor` 后台线程，对下一任务执行 PLAN 预计算，结果存入 `_speculative_spec` 缓存。PLAN 阶段优先消费缓存 spec，避免 LLM 等待延迟。`speculative_agent_executor=None` 时跳过预计算（单任务场景）。

### 6.7 DECOMPOSE State
DECOMPOSE 是可到达的状态（在 `orchestrator._execute_state()` 有独立分支），用于将复杂任务分解为子任务 spec。DECOMPOSE → PLAN 转换由 `TransitionTrigger.DECOMPOSE_COMPLETE` 触发。

---

## 7. 实现状态（2026-05-10 审计后确认）

### 7.1 测试状态

|| 测试 | 结果 | 说明 |
||------|------|------|
||| `pytest tests/` | 55/55 ✅ | CLI + executor + parallel speedup + run_tasks |
||| `benchmark_nexus.py` | 77/77 ✅ | 12 个 benchmark 覆盖所有层 |

**Benchmark 明细**：

|| Benchmark | 覆盖 |
||-----------|------|
|| 1: Self-Evolution | 8/8 |
|| 2: Parallel Subagents | 3/3 |
|| 3: WAL + Checkpoint + Working Buffer | 21/21 |
|| 4: TDD Enforcement | 8/8 |
|| 5: Model Router | 9/9 |
|| 6: MCP Integration | 7/7 |
|| 7: Parallel Speedup (实测) | 3/3 |
|| 8: VerificationPipeline Inline | 4/4 |
|| 9: ToolRegistry Dynamic Loading | 6/6 |
|| 10: Checkpoint Periodic Fix | 4/4 |
|| 11: DECOMPOSE Reachability | 5/5 |
|| 12: Streaming Callback | 5/5 |

### 7.2 已验证的实现细节

| 问题 | 状态 | 说明 |
|------|------|------|
| TDD 默认值 | ✅ 已修复 | `executor.py:147` — `enable_tdd=True` |
| 并行路径 TDD | ✅ 已修复 | `subagent_integration.py` — `run_implementer_with_review(enable_tdd=...)` 传递并启用 TDD 循环 |
| CLAUDE.md 加载 | ✅ 已修复 | `executor._build_system_prompt()` — 自动加载三层合并（全局/项目/目录） |
| run_tasks speculative | ✅ 已实现 | 2+ tasks 时 `speculative_agent_executor` 启用，ACT 时后台预计算下一 PLAN |
| SelfEvo VERIFY 闭环 | ✅ 已确认 | `orchestrator` → `_execute_verify()` → `_learn_from_verification_outcome()` 链路正确 |
| run.py 双重验证 | ✅ 已确认 | CLI 层 `_run_act_gates` 保留作为非阻塞信心检查，非重复 |
| WAL → Agent Loop | ✅ 已确认 | `run_agent_loop(wal=self._wal)` 每次调用正确传递 |

### 7.3 诚实状态（与 README 的差距）

README 吹的 8 大创新，审计后确认：

| README 声称 | 实际状态 | 差距 |
|------------|---------|------|
| Multi-Agent 协作 | 单线程顺序状态机，并行仅限 ACT phase 两个 subagent | 中等 — 顶层状态机未并行化 |
| Self-Evolution VERIFY 闭环 | ✅ 已 wired | 无 |
| TDD Enforcement | ✅ enable_tdd=True 已生效 | 无 |
| WAL + Checkpoint | ✅ 完整实现 | 无 |
| Model Router | ✅ 完整实现 | 无 |
| Working Buffer | ✅ 完整实现 | 无 |
| Streaming | ✅ 回调机制完整 | 无 |
| MCP 集成 | 连接管理完整，缺真实 server 端到端测试 | 小 — 需要真实 MCP server |

---

## 8. Nexus vs Claude Code

| 特性 | Nexus | Claude Code |
|------|-------|-------------|
| Self-Evolution（跨 session 错误学习） | ✅ | ❌ |
| Parallel Subagents（并行实现+审查） | ✅ | ❌ |
| WAL + Checkpoint（崩溃恢复） | ✅ | ❌ |
| TDD Enforcement（RED→GREEN→REFACTOR） | ✅ | ❌ |
| Smart Model Router（cost 优化） | ✅ | ❌ |
| Working Buffer（代码实验沙盒） | ✅ | ❌ |
| Streaming Output | ✅ | ✅ |
| DECOMPOSE State（任务分解） | ✅ | ❌ |
| VerificationPipeline Inline | ✅ | ❌ |

---

## 9. 待完成（TODO）

| 优先级 | 任务 | 备注 |
|--------|------|------|
| P0 | 真实任务端到端测试 | 用实际编程任务验证全流程 |
| P1 | TUI streaming 真实集成 | 当前 TUI 仍传 `streaming_callback=None` |
| P2 | Multi-Agent 协作升级 | 顶层状态机支持多 agent 并行协作（当前是单线程顺序） |
| P2 | MCP 真实 server 连接测试 | 目前是连接管理器，缺真实端到端测试 |
