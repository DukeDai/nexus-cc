# SPEC — Nexus 规格说明书

> **版本**：v4（统一版，合并 v2 + v3）
> **最后更新**：2026-05-04
> **状态**：RalphLoop 核心完成，act-e2e 阶段

---

## 背景与目标

Nexus 是基于 **RalphLoop 状态机** 的自进化编程智能体，核心目标是：

1. **达到** Claude Code 的核心体验完整度（文件操作、Git、安全扫描、项目感知）
2. **超越** Claude Code 通过：TDD 强制、多 Agent 协作、自进化技能、状态可见性

---

## 架构总览

```
User Input
    ↓
[CLAUDE.md 三层合并]
    ↓
RalphLoop Orchestrator（状态机编排）
    ├── PLAN     → 分析任务 → 拆解为原子步骤
    ├── ACT      → 调用 LLM → 解析 ToolCall → 执行工具
    ├── VERIFY   → TDD Gate → 安全扫描 → 测试验证
    └── REFLECT  → 捕获错误 → 判断重试/升级/放弃
    ↓
Git Commit（可选）
```

---

## 核心组件

### RalphLoop Orchestrator

| 文件 | 行数 | 职责 |
|------|------|------|
| `orchestrator.py` | 479 | 主引擎：状态转换、恢复、超时 |
| `agent_loop.py` | 580 | LLM 闭环：PLAN→ACT→VERIFY→REFLECT 各状态的实际执行 |
| `tdd_enforcer.py` | 528 | TDD 强制：RED→GREEN→REFACTOR 循环 |
| `transitions.py` | 287 | 转换表 + 守卫条件 |
| `states.py` | ~43 | 8 状态枚举 |
| `subagent_registry.py` | 259 | 5 专业 Agent 定义 |
| `subagent_integration.py` | 485 | Orchestrator ↔ delegate_task 桥接 |
| `claude_md_loader.py` | 296 | CLAUDE.md 发现 + 项目根检测 + LLM 系统提示构建 |

### Multi-Agent 系统

| Agent | 行数 | 职责 |
|-------|------|------|
| `specifier.py` | ~150 | 需求 → SPEC.md |
| `implementer.py` | ~200 | 代码生成 + TDD 执行 |
| `reviewer.py` | 504 | 质量门 |
| `security.py` | 676 | 安全扫描 |
| `agents/base.py` | ~100 | Agent 基类 |

### Verification Pipeline

| 文件 | 行数 | 职责 |
|------|------|------|
| `tdd_gate.py` | 526 | RED→GREEN→REFACTOR 门 |
| `test_gate.py` | 596 | pytest 执行 + 基线对比 |
| `security_scan.py` | 650 | 密钥/注入/路径遍历扫描 |
| `review_gate.py` | 690 | 独立代码审查 |
| `pipeline.py` | 621 | 管道编排 |

### Context Management

| 文件 | 职责 |
|------|------|
| `monitor.py` | 4-tier 预算监控（PEAK/GOOD/DEGRADING/POOR） |
| `claudemd.py` | CLAUDE.md 三层合并 |
| `checkpoint.py` | 状态检查点 |
| `worktree.py` | Git Worktree 管理 |

### TUI

| 文件 | 行数 | 职责 |
|------|------|------|
| `nexus_tui.py` | 594 | ANSI 实时仪表盘 |
| `app.py` | 567 | Rich Live 主应用 |

### MCP

| 文件 | 行数 | 职责 |
|------|------|------|
| `client.py` | 663 | 异步生命周期管理 |
| `bridge.py` | 644 | 工具桥 + 缓存 + 限流 |
| `presets.py` | ~100 | GitHub/Slack/PostgreSQL 预设 |

### LLM

| 文件 | 行数 | 职责 |
|------|------|------|
| `client.py` | 829 | Anthropic/OpenAI/Ollama 统一接口 |

---

## RalphLoop 状态机

### 状态定义

```
IDLE → PLAN → ACT → VERIFY → REFLECT → (TRANSIT) → PLAN...
         ↓       ↓       ↓         ↓
      ESCALATE COMMIT   ABORT    TRANSIT
```

### 状态详情

| 状态 | 入口条件 | 动作 | 出口条件 |
|------|---------|------|---------|
| **IDLE** | 初始/任务完成 | 加载上下文 | 用户输入 |
| **PLAN** | IDLE + 用户任务 | 拆解任务步骤，识别 CLAUDE.md | 步骤队列 |
| **ACT** | PLAN 完成 | LLM 调用 → ToolCall 执行 | 工具结果 |
| **VERIFY** | ACT 完成 | TDD Gate / Security / Test | 通过/失败 |
| **REFLECT** | VERIFY 完成 | 错误模式捕获，决策 | 重试/升级/提交 |
| **ESCALATE** | 任何状态 | 4 选项：(1)自行修复 (2)求助 (3)简化 (4)放弃 | 返回 ACT/IDLE |
| **COMMIT** | REFLECT 通过 | git add + commit | IDLE |
| **ABORT** | VERIFY 失败 | 保存检查点，回滚 | IDLE |

### TDD 强制门

```
Task Received
    ↓
┌─────────────────┐
│  Write RED Test │  ← LLM 生成测试（预期失败）
└────────┬────────┘
         ↓
┌─────────────────┐
│  Run RED        │  ← pytest 确认失败
└────────┬────────┘
         ↓
┌─────────────────┐
│  Write GREEN    │  ← LLM 生成实现（最小代码）
└────────┬────────┘
         ↓
┌─────────────────┐
│   Run GREEN     │  ← pytest 确认通过
└────────┬────────┘
         ↓
┌─────────────────┐
│  REFACTOR       │  ← 改进代码质量
└────────┬────────┘
         ↓
┌─────────────────┐
│  Final Tests    │  ← 确认重构后仍然通过
└────────┬────────┘
         ↓
      COMMIT
```

### 上下文预算模型

| Tier | 消耗 | 行为 |
|------|------|------|
| **PEAK** | 0-30% | 全速推理，并行 Agent |
| **GOOD** | 30-50% | 正常执行 |
| **DEGRADING** | 50-70% | 减少探索，聚焦已知路径 |
| **POOR** | 70-100% | 紧急检查点，建议升级 |

---

## Subagent 专业分工

| Agent | 输入 | 输出 | 关键能力 |
|-------|------|------|---------|
| **Specifier** | 用户任务描述 | SPEC.md / CLAUDE.md 片段 | 需求澄清、规格生成 |
| **Implementer** | SPEC + 上下文 | 代码 + 工具调用 | TDD 强制、工具执行 |
| **Reviewer** | 代码 + 测试 | Review 报告 | 质量门、代码审查 |
| **Security** | 代码 | 漏洞报告 | 密钥/注入/路径遍历 |
| **Test** | SPEC | RED 测试代码 | 测试生成 |

### 并行执行模式

```
用户任务
    ↓
SpecifierAgent（串行：先理解需求）
    ↓
┌──────────────────────────────────────┐
│ ImplementerAgent（主）                │
│   + ReviewerAgent（并行）            │
│   + SecurityAgent（并行）            │
│   + TestAgent（并行）                │
└──────────────────────────────────────┘
    ↓
RalphLoop REFLECT → 决策
```

---

## CLAUDE.md 三层合并

```
~/.claude/CLAUDE.md        ← 全局规范（工具偏好、安全策略）
project/CLAUDE.md          ← 项目规范（架构决策、约定）
directory/.CLAUDE.md       ← 目录规范（模块规则）
        ↓
    build_llm_system_prompt()
        ↓
    注入 LLM System Prompt
```

---

## 关键差异（vs Claude Code）

| 能力 | Claude Code | Nexus |
|------|------------|-------|
| TDD Enforcement | ❌ 无 | ✅ RED→GREEN→REFACTOR 强制 |
| 多 Agent 协作 | ❌ 无 | ✅ 5 专业 Agent 并行 |
| RalphLoop 状态可见性 | ❌ 黑盒 | ✅ PLAN→ACT→VERIFY→REFLECT |
| 上下文预算感知 | ❌ 隐式 | ✅ 4-tier 显式监控 |
| 自进化技能系统 | ❌ 无 | ✅ 错误→技能捕获 |
| 跨会话 Checkpoint | ❌ 无 | ✅ SQLite 持久化 |
| Skill 系统集成 | ❌ 无 | ✅ Hermes Skill Hub |

---

## 执行计划（已完成 vs 待办）

### ✅ 已完成（里程碑）

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **act-realagents** | RalphLoop orchestrator 真正调用 subagent | ✅ 7/7 测试通过 |
| **feat-subagent-arch** | subagent registry + CLAUDE.md loader + TUI | ✅ 97ba2dc |

### 🔄 当前（act-e2e）

| 任务 | 内容 |
|------|------|
| **act-e2e** | 端到端：用 Nexus 开发一个 REST API（FastAPI） |

### 📋 规划（verify-gap）

| 任务 | 内容 |
|------|------|
| **verify-gap** | 在 10 个任务上与 Claude Code 对比 |

---

## 验收标准

### act-e2e 验收

1. `nexus run --task "Build a REST API with FastAPI"` 能完整执行：
   - 自动生成 SPEC.md
   - TDD RED 测试
   - TDD GREEN 实现
   - pytest 全部通过
   - Git commit

2. TUI 实时显示：PLAN→ACT→VERIFY→REFLECT 状态

### verify-gap 验收

1. 同等任务完成率 ≥ Claude Code
2. TDD 覆盖率 > 80%（Claude Code = 0%）
3. 多 Agent 协作任务占比 > 50%

---

## 文件清单（src/）

```
src/
├── ralphloop/           # 状态机引擎（~3300行）
│   ├── __init__.py
│   ├── orchestrator.py
│   ├── agent_loop.py
│   ├── tdd_enforcer.py
│   ├── transitions.py
│   ├── states.py
│   ├── subagent_registry.py
│   ├── subagent_integration.py
│   └── claude_md_loader.py
├── agents/              # 多智能体（~1600行）
│   ├── __init__.py
│   ├── base.py
│   ├── specifier.py
│   ├── implementer.py
│   ├── reviewer.py
│   └── security.py
├── verification/        # 验证管道（~3000行）
│   ├── __init__.py
│   ├── tdd_gate.py
│   ├── test_gate.py
│   ├── security_scan.py
│   ├── review_gate.py
│   └── pipeline.py
├── context/             # 上下文管理
│   ├── __init__.py
│   ├── monitor.py
│   ├── claudemd.py
│   ├── checkpoint.py
│   └── worktree.py
├── tui/                # 交互 UI（~1200行）
│   ├── __init__.py
│   ├── nexus_tui.py
│   ├── app.py
│   ├── state_view.py
│   ├── context_view.py
│   ├── agent_view.py
│   └── task_view.py
├── mcp/                # MCP 集成（~1500行）
│   ├── __init__.py
│   ├── client.py
│   ├── bridge.py
│   ├── connection.py
│   ├── config.py
│   ├── presets.py
│   └── integration.py
├── llm/                # LLM 客户端
│   ├── __init__.py
│   ├── client.py
│   └── model_router.py
├── tools/              # 工具集
│   ├── __init__.py
│   ├── base.py
│   ├── bash.py
│   ├── read.py
│   ├── write.py
│   ├── edit.py
│   ├── glob.py
│   ├── grep.py
│   ├── git.py
│   └── web_search.py
├── hooks/              # 事件钩子
│   ├── __init__.py
│   ├── hook_manager.py
│   ├── pre_tool_hook.py
│   ├── post_tool_hook.py
│   └── integration.py
└── skills/             # 自进化技能
    ├── __init__.py
    └── author.py
```

---

## Git 历史

```
97ba2dc feat: RalphLoop subagent architecture + CLAUDE.md loader + Nexus TUI
416858b feat: subagent registry, CLAUDE.md loader, subagent integration layer
fb14ea1 docs: ULTIMATE_GOAL — gap analysis vs Claude Code
2917207 feat: RalphLoop real LLM-driven closed loop + TDD enforcement
14bc3ad feat: robust unified diff/patch
643baa4 feat: nexus_core.py CC Switch support
c92a3dc feat: Nexus v2 core — RalphLoop agent + LLM tools + nexus_core.py
fda1191 docs: rewrite README with complete feature summary
8de8ee3 Initial commit: Nexus v1.0
```

---

*SPEC 统一版 v4 — 2026-05-04*
