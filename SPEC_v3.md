# SPEC v3 — Real TDD Enforcement + Agentic Loop

## 背景

v2 完成了 LLM 客户端和工具系统的骨架建设，act-diff 完成了 robust diff/patch。v3 的核心目标是让 **RalphLoop 真正跑起来**——从 LLM 生成代码 → 执行工具 → 验证结果 → 反馈修正，形成完整的自主闭环。

---

## 目标

实现真正的 **LLM-driven TDD enforcement**，让 Nexus 能像专业开发者一样：

1. 先写 RED 测试（明确预期行为）
2. 写 GREEN 实现（最小化通过测试）
3. REFACTOR 改进（保持行为，提升质量）

---

## 核心架构

### RalphLoop Orchestrator（编排器）

```
┌─────────────────────────────────────────────────────┐
│                    RalphLoop                         │
│  ┌──────┐   ┌──────┐   ┌───────┐   ┌────────┐       │
│  │ PLAN │ → │ ACT  │ → │VERIFY │ → │ REFLECT│       │
│  └──────┘   └──────┘   └───────┘   └────────┘       │
│                 ↑              ↓                     │
│                 └──── ABORT/ESCALATE ────┘           │
└─────────────────────────────────────────────────────┘
```

### 状态流转

- **PLAN**: 分析任务 → 拆解为原子步骤 → 识别 CLAUDE.md 约束
- **ACT**: 调用 LLM → 解析 ToolCall → 执行工具 → 收集结果
- **VERIFY**: 比对实际输出 vs 预期 → TDD Gate 判断
- **REFLECT**: 捕获错误模式 → 判断是否需要重试/升级/放弃

### TDD 强制门

```
Task Received
    ↓
┌─────────────────┐
│  Write RED Test │  ← LLM 生成测试（预期失败）
└────────┬────────┘
         ↓
┌─────────────────┐
│  Write GREEN    │  ← LLM 生成实现（最小代码）
└────────┬────────┘
         ↓
┌─────────────────┐
│   Run Tests     │  ← 实际执行 pytest
└────────┬────────┘
         ↓
    ┌────┴────┐
    │ PASS?   │
    └────┬────┘
      Y       N
      ↓       ↓
┌─────────┐ ┌─────────────────┐
│REFACTOR │ │ DEBUG LOOP      │
│ & COMMIT│ │ (max 3 iter)   │
└─────────┘ └─────────────────┘
```

---

## 实现计划

### Phase 1: RalphLoop Core（编排器 + 状态机）

**文件**：`src/ralphloop/`

| 文件 | 职责 |
|------|------|
| `orchestrator.py` | 主循环：PLAN→ACT→VERIFY→REFLECT |
| `states.py` | 状态定义（State enum + 转换规则） |
| `context.py` | 上下文管理（4-tier budget：PEAK/GOOD/DEGRADING/POOR） |

**关键设计**：
- 每个状态可配置 `timeout`、`max_retries`、`fallback_action`
- ABORT 条件可配置（如安全违规、循环超限）
- ESCALATE 可触发人工介入或降级策略

### Phase 2: LLM Client → ToolCall 闭环

**文件**：`src/llm/`

| 文件 | 职责 |
|------|------|
| `client.py` | Anthropic/OpenAI/Ollama 统一接口 |
| `model_router.py` | 模型选择策略 |

**ToolCall 协议**（Anthropic tool_use）：

```python
class ToolCall:
    id: str          # tool_use id
    name: str        # bash / read / write / edit / ...
    input: dict      # 参数字典
    # ...
```

**执行管道**：
```
LLM Response (with tool_calls)
    → ToolCallParser.parse()
    → ToolRegistry.get(name)
    → tool.execute(**input)
    → ToolResult
    → 反馈给 LLM（继续对话）
```

### Phase 3: TDD Enforcement（测试优先驱动）

**文件**：`src/verification/`

| 文件 | 职责 |
|------|------|
| `tdd_gate.py` | TDD 强制门：RED→GREEN→REFACTOR |
| `test_runner.py` | pytest 执行器 |
| `security_gate.py` | 安全扫描 |

**TDDGate 状态机**：

```
IDLE → WRITING_RED → RUN_RED(RED) → WRITING_GREEN →
RUN_GREEN → (PASS? → REFACTORING → DONE)
                       ↓ FAIL
                  DEBUG_LOOP(max 3)
                       ↓ FAIL
                    ESCALATE
```

### Phase 4: Multi-Agent Coordination（多 agent 协作）

**Agent 分工**：

| Agent | 输入 | 输出 |
|-------|------|------|
| `SpecifierAgent` | 需求描述 | SPEC.md / CLAUDE.md 片段 |
| `ImplementerAgent` | SPEC + 上下文 | 代码（ACT 阶段主力） |
| `ReviewerAgent` | 代码 + 测试 | Review 报告 + 修改建议 |
| `SecurityAgent` | 代码 | 安全漏洞报告 |

**Agent 间通信**：通过共享 `TaskContext`（带锁）进行状态共享。

### Phase 5: Session Persistence（跨会话恢复）

**文件**：`src/session/`

- 对话历史持久化到 JSON
- 每次 ACT 后自动 checkpoint
- 重启后可恢复到任意 checkpoint

---

## 关键差异点（vs Claude Code）

| 能力 | Claude Code | Nexus v3 |
|------|------------|----------|
| **TDD Enforcement** | ❌ 无 | ✅ 强制 RED→GREEN→REFACTOR |
| **状态机可见性** | ❌ 黑盒 | ✅ RalphLoop 全流程可见 |
| **多模型路由** | ❌ | ✅ Anthropic/OpenAI/Ollama |
| **安全扫描** | ⚠️ 插件 | ✅ 内置 + 可配置 |
| **自进化** | ❌ | ✅ 错误 → 技能捕获 |

---

## 验收标准

### 最小可用（v3-alpha）

1. `nexus_core.py --model sonnet "Create a simple HTTP server"` 能：
   - 分解任务为 PLAN
   - 调用 LLM 生成代码
   - 用工具创建文件
   - TDD Gate 通过

2. 测试覆盖率：
   - RED test 失败 → GREEN 实现通过 → REFACTOR 改进

### 完整 v3

1. 同等任务 vs Claude Code 对比（verify-gap）
2. 安全扫描误报率 < 5%
3. Session 恢复后状态一致

---

## 文件清单

```
src/
├── llm/
│   ├── client.py        # ✅ 已有，需增强 ToolCall 解析
│   ├── model_router.py  # ✅ 已有
│   └── __init__.py
├── tools/
│   ├── bash.py          # ✅ 已有
│   ├── read.py          # ✅ 已有
│   ├── write.py         # ✅ 已有
│   ├── edit.py          # ✅ 已有
│   ├── base.py          # ✅ 已有
│   ├── glob.py          # ✅ 已有
│   ├── grep.py          # ✅ 已有
│   ├── git.py           # ✅ 已有
│   ├── web_search.py    # ✅ 已有
│   └── __init__.py
├── engine/
│   ├── executor.py      # ✅ 已有，需增强 LLM 回调
│   ├── registry.py      # ✅ 已有
│   └── __init__.py
├── ralphloop/
│   ├── orchestrator.py  # ⚠️ 需重写，真正的主循环
│   ├── states.py        # ⚠️ 需增强状态定义
│   ├── context.py        # 🆕 新增
│   └── __init__.py
├── verification/
│   ├── tdd_gate.py       # ⚠️ 需重写，真正的 TDD
│   ├── test_runner.py    # 🆕 新增
│   ├── security_gate.py  # ⚠️ 已有 stub，需增强
│   └── __init__.py
├── agents/
│   ├── specifier.py      # ⚠️ 需重写，真正调用 LLM
│   ├── implementer.py    # ⚠️ 需重写，真正调用 LLM
│   ├── reviewer.py       # ⚠️ 需重写，真正调用 LLM
│   ├── security.py       # ⚠️ 需重写，真正调用 LLM
│   └── __init__.py
├── session/
│   ├── manager.py        # ⚠️ 已有 stub，需增强
│   └── __init__.py
└── mcp/
    ├── client.py         # ⚠️ 已有 stub
    └── __init__.py
```

---

## 执行顺序

```
act-realagents/
├── Step 1: RalphLoop Orchestrator 重写（真正的主循环）
├── Step 2: ToolCall 协议实现（LLM → 工具 → 结果）
├── Step 3: TDD Gate 实现（RED→GREEN→REFACTOR 循环）
├── Step 4: Agent 协作（4 agent 分工）
└── Step 5: 端到端测试（用 Nexus 开发真实项目）
```

---

*最后更新：2026-05-03*
