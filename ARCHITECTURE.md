# Nexus Architecture — RalphLoop 核心架构

> **版本**：v6（重写版）
> **最后更新**：2026-05-08
> **目标**：超越 Claude Code/Codex/OpenCode 的下一代 coding AI agent
> **诚实状态**：RalphLoopExecutor 6层已全部 wired；核心编排逻辑从单线程循环升级为真实 6 层 executor

---

## 背景与目标

Nexus 是基于 **RalphLoop 状态机** 的自进化编程智能体，核心目标：

1. **达到** Claude Code 的核心体验完整度（文件操作、Git、安全扫描、项目感知）
2. **超越** Claude Code 通过：TDD 强制、Multi-Agent 协作、自进化技能、状态可见性

### RalphLoop 状态机

```
PLAN → ACT → VERIFY → REFLECT → (COMMIT | RETRY | ESCALATE | ABORT)
```

每个状态由对应的 `_execute_*` 方法在 `RalphLoopExecutor` 里实现。

---

## 1. 顶层执行流

```
CLI (nexus.py / src/cli/main.py)
  └─ run command (src/cli/commands/run.py)
       └─ RalphLoopExecutor.run_task()
            ├─ WALManager.log_transition()        [状态转换记录]
            ├─ RalphLoopOrchestrator.run_loop()   [状态机驱动]
            │    ├─ _execute_plan()    → LLM + tools + WAL
            │    ├─ _execute_act()     → SubagentIntegration (并行)
            │    ├─ _execute_verify()  → verification pipeline
            │    └─ _execute_reflect() → SelfEvolutionEngine
            ├─ CheckpointManager.save()           [周期性快照]
            └─ SelfEvolutionEngine.on_task_done() [跨session学习]
```

---

## 2. RalphLoopExecutor（6层统一入口）

**文件**：`src/ralphloop/executor.py`（`SixLayerRalphLoopExecutor` 类）

| 层 | 组件 | 文件 | 初始化参数 |
|----|------|------|-----------|
| 1 | WALManager | `src/context/wal.py` | `enable_wal=True` |
| 2 | CheckpointManager | `src/context/checkpoint.py` | `enable_checkpoint=True` |
| 3 | SelfEvolutionEngine | `src/self_evolution/engine.py` | `enable_self_evolution=True` |
| 4 | ModelRouter | `src/llm/model_router.py` | `enable_model_router=True` |
| 5 | SubagentIntegration | `src/ralphloop/subagent_integration.py` | `enable_parallel_subagents=True` |
| 6 | TDDEnforcer | `src/ralphloop/tdd_enforcer.py` | `enable_tdd=True` |

**关键约束**：WAL 实例通过 `wal=self._wal` 参数传递给每个 `run_agent_loop` 调用——这保证了崩溃恢复可以重放完整日志。

---

## 3. 核心组件详解

### 3.1 状态机 & Agent Loop

| 文件 | 职责 |
|------|------|
| `src/ralphloop/states.py` | `LoopState` enum（PLAN/ACT/VERIFY/REFLECT/COMMIT/RETRY/ESCALATE/ABORT） |
| `src/ralphloop/transitions.py` | 状态转换规则（什么条件下允许什么转换） |
| `src/ralphloop/orchestrator.py` | `RalphLoopOrchestrator` — 状态机循环实现 |
| `src/ralphloop/agent_loop.py` | `run_agent_loop()` — 单次 LLM+工具闭环，含 WAL 支持 |

### 3.2 LLM 层

| 文件 | 职责 |
|------|------|
| `src/llm/client.py` | `LLMClient` — streaming / complete 两种调用模式 |
| `src/llm/model_router.py` | `ModelRouter` — 根据任务类型（SIMPLE/COMPLEX/DISTRIBUTED）选择模型 |

**ModelRouter 路由逻辑**（在 `SubagentIntegration._get_llm_client` 中使用）：
- `NexusTaskType.SIMPLE` → MiniMax
- `NexusTaskType.COMPLEX` → Claude
- `NexusTaskType.DISTRIBUTED` → GPT-4o

### 3.3 Subagent 系统

| 文件 | 职责 |
|------|------|
| `src/ralphloop/subagent_registry.py` | 5种 Agent 注册表（Specifier/Implementer/Reviewer/Security/SCAFFOLD） |
| `src/ralphloop/subagent_integration.py` | `_execute_act_parallel()` — ThreadPoolExecutor 并行执行 Implementer + Reviewer |
| `src/agents/base.py` | Agent 基类 |
| `src/agents/specifier.py` | 任务分解 Agent |
| `src/agents/implementer.py` | 实现 Agent |
| `src/agents/reviewer.py` | 审查 Agent |
| `src/agents/security.py` | 安全扫描 Agent |

### 3.4 Context 层（WAL/Checkpoint/监控）

| 文件 | 职责 |
|------|------|
| `src/context/wal.py` | `WALManager` — Write-Ahead Log，状态转换记录，崩溃后恢复计划生成 |
| `src/context/checkpoint.py` | `CheckpointManager` — 完整状态快照，SQLite 持久化 |
| `src/context/monitor.py` | `ContextMonitor` — 4-tier 上下文预算监控（PEAK/GOOD/DEGRADING/POOR） |
| `src/context/claudemd.py` | CLAUDE.md 三层合并加载器（全局/项目/目录） |
| `src/context/working_buffer.py` | 工作缓冲区 |
| `src/context/worktree.py` | Git worktree 管理 |

### 3.5 验证管道（ACT 后自动触发）

| 文件 | 职责 |
|------|------|
| `src/verification/pipeline.py` | `VerificationPipeline.run()` — 顺序执行所有验证 |
| `src/verification/security_scan.py` | 内置安全扫描（hardcoded secret 检测等） |
| `src/verification/test_gate.py` | pytest 自动发现并运行 |
| `src/verification/tdd_gate.py` | TDD 测试存在性检查 |
| `src/verification/review_gate.py` | 代码审查门控 |

### 3.6 自进化引擎

| 文件 | 职责 |
|------|------|
| `src/self_evolution/engine.py` | `SelfEvolutionEngine` — 错误模式捕获 → 技能库 |
| `src/skills/capture.py` | 技能捕获 |
| `src/skills/author.py` | 技能创作 |
| `src/skills/loader.py` | 技能加载 |

### 3.7 工具层

| 文件 | 工具 |
|------|------|
| `src/tools/bash.py` | `BashTool` — 命令执行（shell=False 安全） |
| `src/tools/edit.py` | `EditTool` — 块编辑 |
| `src/tools/write.py` | `WriteTool` — 文件写入 |
| `src/tools/read.py` | `ReadTool` — 文件读取 |
| `src/tools/glob.py` | `GlobTool` — 文件搜索 |
| `src/tools/grep.py` | `GrepTool` — 内容搜索 |
| `src/tools/git.py` | `GitTool` — Git 操作 |
| `src/tools/web_search.py` | `WebSearchTool` — 网络搜索 |
| `src/tools/base.py` | `Tool` 基类 + `ToolResult` |
| `src/engine/registry.py` | `ToolRegistry` — 工具注册表 |
| `src/engine/executor.py` | `ToolExecutor` — 工具执行器 + `HookManager` |

### 3.8 Hook 系统

| 文件 | 职责 |
|------|------|
| `src/hooks/hook_manager.py` | `HookManager` — pre/post 工具钩子注册表 |
| `src/hooks/pre_tool_hook.py` | 前置钩子 |
| `src/hooks/post_tool_hook.py` | 后置钩子 |
| `src/hooks/integration.py` | 钩子集成点 |

### 3.9 MCP 集成

| 文件 | 职责 |
|------|------|
| `src/mcp/bridge.py` | MCP bridge — 本地工具暴露为 MCP |
| `src/mcp/client.py` | MCP client |
| `src/mcp/config.py` | MCP 配置 |
| `src/mcp/connection.py` | 连接生命周期 |
| `src/mcp/integration.py` | MCP 集成入口 |
| `src/mcp/presets.py` | 预设 MCP server 配置 |

### 3.10 Session 管理

| 文件 | 职责 |
|------|------|
| `src/session/manager.py` | `SessionManager` — session 创建/恢复 |
| `src/session/store.py` | `SessionStore` — session 持久化 |
| `src/session/models.py` | `Session` / `SessionSummary` 数据模型 |

### 3.11 TUI

| 文件 | 职责 |
|------|------|
| `src/tui/nexus_tui.py` | `NexusTUI` — Rich TUI 主应用 |
| `src/tui/state_view.py` | 状态视图 |
| `src/tui/agent_view.py` | Agent 视图 |
| `src/tui/context_view.py` | 上下文视图 |
| `src/tui/task_view.py` | 任务视图 |
| `src/tui/approval.py` | 审批视图 |
| `src/tui/app.py` | TUI App 基类 |
| `src/tui/input_handler.py` | 输入处理 |

### 3.12 CLI

| 文件 | 职责 |
|------|------|
| `src/cli/main.py` | Click 根命令 |
| `src/cli/commands/run.py` | `run` 命令 — 调用 `RalphLoopExecutor` |
| `src/cli/commands/tui.py` | `tui` 命令 |
| `src/cli/commands/session.py` | `session` 命令（list/resume） |
| `src/cli/commands/mcp.py` | `mcp` 命令（list/presets） |
| `src/cli/commands/skills.py` | `skills` 命令 |
| `src/cli/commands/cost.py` | `cost` 命令 |

---

## 4. 目录结构

```
src/
├── agents/          # Subagent 实现（Specifier/Implementer/Reviewer/Security）
├── cli/             # Click CLI 命令
│   └── commands/   # run / tui / session / mcp / skills / cost
├── context/         # WAL / Checkpoint / Monitor / CLAUDE.md
├── engine/          # ToolRegistry / ToolExecutor / HookManager
├── hooks/           # pre/post 工具钩子
├── llm/             # LLMClient / ModelRouter
├── mcp/             # MCP bridge / client / config
├── ralphloop/       # 核心状态机 + 6层 executor
│   ├── executor.py        # RalphLoopExecutor（入口）
│   ├── orchestrator.py   # 状态机循环
│   ├── agent_loop.py      # 单次 LLM+工具闭环
│   ├── subagent_integration.py  # 并行 subagent
│   ├── subagent_registry.py     # Agent 注册表
│   ├── tdd_enforcer.py   # TDD 强制
│   ├── states.py          # 状态 enum
│   ├── transitions.py     # 转换规则
│   └── ...
├── self_evolution/  # 自进化引擎 + 技能库
├── session/         # Session 持久化
├── skills/          # 技能捕获/创作/加载
├── tools/           # 工具实现（bash/edit/read/glob/grep/git/web_search/write）
├── tui/             # Rich TUI
└── verification/    # ACT 后验证管道（security / pytest / tdd / review）
```

---

## 5. 使用方法

```python
from src.ralphloop.executor import RalphLoopExecutor

executor = RalphLoopExecutor(
    workdir="./project",
    enable_wal=True,                # WAL 日志化
    enable_checkpoint=True,         # 检查点快照
    enable_self_evolution=True,     # 自进化
    enable_model_router=True,       # 模型路由
    enable_parallel_subagents=True, # Implementer+Reviewer 并行
    enable_tdd=True,               # TDD prompt enforcement
)

result = executor.run_task("Create a REST API with FastAPI")
```

---

## 6. 关键设计决策

### 6.1 WAL → Agent Loop Wiring
每个 `_execute_plan/review` 调用 `run_agent_loop` 时都传入 `wal=self._wal`。崩溃恢复时 WAL 日志可以被回放，生成恢复计划。这是 crash-recovery 的核心。

### 6.2 Subagent 并行
`_execute_act_parallel` 使用 `ThreadPoolExecutor` 同时运行 Implementer 和 Reviewer。Security scan 在 Reviewer 完成后串行执行。所有 subagent 调用同一个 `wal=self._wal`。

### 6.3 ModelRouter 在 Subagent 级别
每个 subagent 启动时通过 `_get_llm_client` 查询 `ModelRouter`，根据任务类型获取对应的 LLM client 和 API key。

### 6.4 TDD 是 Prompt-Based
没有用单独的进程或容器来强制 TDD。TDDEnforcer 通过在 system prompt 里注入 TDD 指令，并在 verification pipeline 的 `tdd_gate.py` 里检查测试文件存在性。

---

## 7. 测试状态

| 测试 | 结果 | 说明 |
|------|------|------|
| `pytest tests/` | 19/19 ✅ | CLI 命令覆盖 |
| `benchmark_nexus.py` | 39/39 ✅ | 核心 6 层 executor 验证 |
| mypy | 0 errors (src/) | 完整类型注解 |

---

## 8. 待完成（TODO）

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | 集成测试：6层 executor 单元测试 | 待做 |
| P1 | ARCHITECTURE.md 重写（诚实版） | ✅ 本次完成 |
| P1 | TDD 强制：从 prompt-based 升级为进程级隔离 | 待讨论 |
| P2 | MCP 集成：真实 MCP server 连接测试 | 待做 |
| P2 | Session 跨进程恢复端到端测试 | 待做 |
