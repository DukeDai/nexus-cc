# Nexus — 下一代自主编程智能体

Nexus 是基于 **RalphLoop 状态机** 的 AI 编程智能体，通过显式状态转换 + 多 Agent 协作 + TDD 强制，超越 Claude Code 的隐式推理可靠性。

> RalphLoop = Ralph（北极燕鸥 🦅）+ Loop（闭环）— 像 Ralph 一样快速、持久、目标明确地完成编程任务。

---

## 核心创新

| 特性 | Claude Code | Nexus |
|------|------------|-------|
| TDD 强制 | 建议 | **每次提交前必须 RED→GREEN→REFACTOR** |
| 多 Agent 协作 | ❌ 无 | **Specifier/Implementer/Reviewer/Security 并行** |
| 状态可见性 | 黑盒 | **RalphLoop PLAN→ACT→VERIFY→REFLECT 全流程可见** |
| 项目感知 | CLAUDE.md | **三层合并（全局/项目/目录）** |
| 上下文预算 | 隐式 | **4-tier 显式监控 PEAK/GOOD/DEGRADING/POOR** |
| 安全扫描 | 插件 | **每次提交前强制内置扫描** |
| 自进化技能 | ❌ 无 | **错误→模式捕获→技能库** |
| Subagent 并行 | ❌ 无 | **delegate_task 多 Agent 并行** |
| 会话持久化 | 基础 | **SQLite 检查点恢复** |

---

## 安装

```bash
git clone https://github.com/DukeDai/nexus-cc.git
cd nexus-cc
pip install -e .
```

---

## 快速开始

```bash
# RalphLoop 核心任务执行
nexus run --task "Create a REST API with FastAPI"

# 交互式 TUI（实时状态仪表盘）
nexus tui

# 会话管理
nexus session list
nexus session resume <session-id>

# MCP 服务器
nexus mcp list
nexus mcp add github "npx github-mcp-server"
nexus mcp presets

# Hook 管理
nexus hooks list
nexus hooks add pre-commit ./scripts/security-check.sh
```

---

## RalphLoop 状态机

```
  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐
  │  PLAN   │───▶│   ACT   │───▶│ VERIFY  │───▶│  REFLECT │
  └────┬────┘    └────┬────┘    └────┬────┘    └────┬─────┘
       │              │              │              │
       ▼              ▼              ▼              ▼
  ┌─────────┐    ┌─────────┐  ┌─────────┐   ┌─────────┐
  │ESCALATE │    │ COMMIT  │  │  ABORT  │   │  TRANS  │
  └─────────┘    └─────────┘  └─────────┘   └─────────┘
```

**状态流转**：PLAN → ACT → VERIFY → REFLECT → (TRANSIT) → PLAN...

**升级选项**：(1) 自行修复 (2) 求助同事 (3) 简化需求 (4) 放弃任务

---

## TDD 强制循环

```
用户: "实现登录功能"
    ↓
┌──────────────────┐
│ 1. RED           │ ← 写测试（预期失败）
│    Write Test    │
└────────┬─────────┘
         ↓
┌──────────────────┐
│ 2. GREEN         │ ← 写实现（最小代码）
│    Write Impl    │
└────────┬─────────┘
         ↓
┌──────────────────┐
│ 3. Run pytest    │
└────────┬─────────┘
         ↓
    ┌────┴────┐
    │ PASS?   │
    └────┬────┘
      Y       N
      ↓       ↓
  ┌────────┐ ┌──────────────────┐
  │REFACTOR│ │ DEBUG LOOP(≤3) │
  │& COMMIT│ └──────────────────┘
  └────────┘         ↓ FAIL
                 ┌─────────┐
                 │ESCALATE │
                 └─────────┘
```

---

## 架构

```
Nexus
├── RalphLoop              # 状态机编排引擎
│   ├── orchestrator.py    # 主引擎（479行）
│   ├── agent_loop.py      # LLM 闭环执行（580行）
│   ├── tdd_enforcer.py    # TDD 强制（528行）
│   ├── transitions.py     # 转换表（287行）
│   ├── states.py          # 8 状态枚举
│   ├── subagent_registry.py    # 5 专业 Agent
│   ├── subagent_integration.py  # Orchestrator↔delegate_task 桥接
│   └── claude_md_loader.py      # CLAUDE.md 三层合并
├── agents/                 # 多智能体专业化
│   ├── specifier.py       # 需求 → 规格
│   ├── implementer.py     # TDD 强制执行
│   ├── reviewer.py        # 质量门（504行）
│   └── security.py        # 安全扫描（676行）
├── verification/          # 提交前验证管道
│   ├── tdd_gate.py        # 测试先行门（526行）
│   ├── test_gate.py       # 基线对比（596行）
│   ├── security_scan.py   # 密钥/注入/路径遍历（650行）
│   ├── review_gate.py     # 独立审查（690行）
│   └── pipeline.py        # 验证管道编排（621行）
├── context/               # 上下文管理
│   ├── monitor.py         # 4-tier 预算监控
│   ├── claudemd.py        # CLAUDE.md 三层合并
│   ├── checkpoint.py      # 状态检查点
│   └── worktree.py        # Git Worktree 管理
├── tui/                    # 交互式终端 UI
│   ├── nexus_tui.py       # ANSI 实时仪表盘（594行）
│   └── app.py             # Rich Live 主应用（567行）
├── mcp/                    # MCP 服务器集成
│   ├── client.py          # 异步生命周期（663行）
│   ├── bridge.py          # 工具桥 + 缓存 + 限流（644行）
│   └── presets.py         # GitHub/Slack/PostgreSQL 预设
└── llm/
    └── client.py          # Anthropic/OpenAI/Ollama 统一（829行）
```

**总代码量**：21,350 行 across 60+ 文件

---

## 上下文预算模型

| 层级 | 消耗 | 行为 |
|------|------|------|
| **PEAK** | 0-30% | 全速执行复杂推理，并行 Agent |
| **GOOD** | 30-50% | 正常执行，frontmatter 优先 |
| **DEGRADING** | 50-70% | 减少探索，聚焦已知路径 |
| **POOR** | 70-100% | 紧急：检查点保存，建议升级 |

---

## Subagent 专业分工

| Agent | 职责 | 输入 | 输出 |
|-------|------|------|------|
| **Specifier** | 需求分析 | 用户任务描述 | SPEC.md / CLAUDE.md 片段 |
| **Implementer** | 代码生成 | SPEC + 上下文 | 代码 + 工具调用 |
| **Reviewer** | 质量审查 | 代码 + 测试 | Review 报告 + 修改建议 |
| **Security** | 安全扫描 | 代码 | 漏洞报告 |
| **Test** | 测试生成 | SPEC | RED 测试代码 |

---

## CLAUDE.md 三层合并

```
~/.claude/CLAUDE.md      ← 全局规范（工具偏好、安全策略）
project/CLAUDE.md        ← 项目规范（架构决策、约定）
directory/.CLAUDE.md     ← 目录规范（模块规则、local overrides）
        ↓
    build_llm_system_prompt()
        ↓
    注入 LLM System Prompt
```

---

## 文件统计

- **60+ Python 文件**
- **~21,350 行代码**
- **9 个模块包**
- **7/7 集成测试通过**

---

## 状态：已完成 vs 进行中

### ✅ 已完成
- RalphLoop 状态机 + orchestrator
- LLM-driven agent_loop（真正调用 LLM + 工具闭环）
- TDD Enforcer（RED→GREEN→REFACTOR）
- CLAUDE.md loader（三层合并）
- Subagent registry + SubagentIntegration
- Nexus TUI（ANSI 实时仪表盘）
- verification pipeline（tdd_gate, security_scan, review_gate, test_gate）
- MCP bridge + presets + connection lifecycle
- 5 专业 Agent（Specifier, Implementer, Reviewer, Security, Test）

### 🔄 下一轮（act-e2e）
- 端到端真实任务完成（用 Nexus 开发一个 REST API）
- 与 Claude Code 相同任务对比测试

### 📋 规划中
- 自进化技能系统（错误→技能捕获）
- 智能 Model 路由（小任务用小模型）
- 跨会话 Checkpoint 持久化
- Tool call 流式输出

---

## License

MIT
