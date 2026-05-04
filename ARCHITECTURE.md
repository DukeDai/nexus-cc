# Nexus Architecture — RalphLoop 核心架构

> **版本**：v5（最终版）
> **最后更新**：2026-05-04
> **目标**：超越 Claude Code/Codex/OpenCode 的下一代 coding AI agent

---

## 背景与目标

Nexus 是基于 **RalphLoop 状态机** 的自进化编程智能体，核心目标是：

1. **达到** Claude Code 的核心体验完整度（文件操作、Git、安全扫描、项目感知）
2. **超越** Claude Code 通过：TDD 强制、Multi-Agent 协作、自进化技能、状态可见性

### RalphLoop 状态机

```
PLAN → ACT → VERIFY → REFLECT → (COMMIT | RETRY | ESCALATE | ABORT)
```

---

## 6 层架构 (RalphLoopExecutor)

```
┌─────────────────────────────────────────────────────┐
│  RalphLoopExecutor - 统一执行器                       │
├─────────────────────────────────────────────────────┤
│  1. WALManager         - 状态转换记录，崩溃恢复       │
│  2. CheckpointManager  - 完整状态快照                │
│  3. SelfEvolutionEngine - 跨session学习              │
│  4. ModelRouter        - 智能模型路由                 │
│  5. SubagentIntegration - 并行Implementer+Reviewer   │
│  6. TDDEnforcer        - RED→GREEN→REFACTOR 强制     │
└─────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 文件 | 功能 |
|------|------|------|
| RalphLoopExecutor | `src/ralphloop/executor.yp` | 统一执行入口 |
| RalphLoop Orchestrator | `src/ralphloop/orchestrator.py` | 状态机 |
| WALManager | `src/context/wal.py` | write_file-Ahead Log |
| CheckpointManager | `src/context/checkpoint.py` | 状态快照 |
| SelfEvolutionEngine | `src/self_evolution/engine.py` | 跨session学习 |
| ModelRouter | `src/llm/model_router.py` | 智能模型选择 |
| SubagentIntegration | `src/ralphloop/subagent_integration.py` | 并行Agent |
| TDDEnforcer | `src/ralphloop/tdd_enforcer.py` | TDD流程 |

---

## 使用方法

```python
from src.ralphloop.executor import RalphLoopExecutor

executor = RalphLoopExecutor(
    workdir="./project",
    enable_wa l=True,              # WAL日志
    enable_checkpoint=True,       # 检查点
    enable_self_evolution=True,   # 自进化
    enable_model_router=True,     # 模型路由
    enable_parallel_subagents=True, # 并行subagent
    enable_tdd=True,              # TDD
)

result = executor.run_task("Create a REST API with FastAPI")
```

---

## CLI 命令

```bash
# 运行任务
python nexus.py run --task "实现用户登录API"

# 交互模式
python nexus.py tui

# Session 管理
python nexus.py session list
python nexus.py session resume <id>
```

---

## 当前状态

| 任务 | 状态 | 日期 |
|------|------|------|
| RalphLoopExecutor 核心 | ✅ 完成 | 2026-05-01 |
| 6 层架构实现 | ✅ 完成 | 2026-05-02 |
| E2E 测试 (REST API) | ✅ 完成 | 2026-05-04 |
| Claude Code 对比 | ✅ 完成 | 2026-05-04 |

### 测试详情

**测试任务**：创建 Flask REST API with TODO endpoints
- GET /todos
- POST /todos  
- DELETE /todos/<id>

**Nexus 输出**：`app.py` (57行) + `requirements.txt`
**Claude Code 输出**：`app.py` (49行) + `requirements.txt`

两者均通过功能验证。