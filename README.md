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

## 配置

### 环境变量

```bash
# 必须设置
export ANTHROPIC_API_KEY="sk-ant-..."    # Anthropic API Key（主要认证方式）

# 可选配置
export ANTHROPIC_AUTH_TOKEN=""            # 备用认证 Token（某些部署方式需要）
export ANTHROPIC_BASE_URL=""               # API 代理/网关地址（用于代理/内网/兼容接口）
export ANTHROPIC_MODEL=""                  # 指定模型，默认 claude-sonnet-4-20250514
export NEXUS_PROVIDER="anthropic"          # LLM 提供商（anthropic/openai/ollama），默认 anthropic
```

### 依赖

```bash
pip install readchar>=4.0    # TUI 命令输入支持
```

---

## 快速开始

```bash
# RalphLoop 核心任务执行 (推荐)
python nexus.py run --task "Create a REST API with FastAPI"

# 交互式 TUI（实时监控 + 交互命令）
python nexus.py tui -t "Create a REST API with FastAPI" -C /path/to/project

# TUI 不带任务（空队列，仅监控）
python nexus.py tui

# 会话管理
python nexus.py session list
python nexus.py session resume <session-id>

# MCP 服务器
python nexus.py mcp list
python nexus.py mcp presets

# Skills 管理
python nexus.py skills list
```

---

## TUI 交互命令

启动 TUI 后，底部命令行可实时输入：

| 命令 | 说明 |
|------|------|
| `status` | 显示当前状态 |
| `help` | 显示帮助 |
| `approve` | 批准当前操作 |
| `reject` | 拒绝当前操作 |
| `retry` | 重试当前任务 |
| `skip` | 跳过当前任务 |
| `undo` | 撤销（待实现） |
| `quit` / `exit` | 退出 TUI |

**交互式 Approval**：当 TUI 暂停在 DEGRADING 状态或危险命令检测时，输入 `approve`/`reject` 控制是否继续。

**Escalation 响应**：当任务重试超限时，底部显示 `1=force-merge 2=rewrite 3=abandon 4=decompose`，直接按数字选择。

---

## 📋 待办清单

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
- **19/19 CLI 测试通过**
- **mypy: src/ 0 errors**

### ✅ 已完成 (2026-05-05)

**核心架构**
- ✅ RalphLoop 状态机 + orchestrator
- ✅ LLM-driven agent_loop（真正调用 LLM + 工具闭环）
- ✅ TDD Enforcer 完整集成（RED→GREEN→REFACTOR）
- ✅ CLAUDE.md loader（三层合并）
- ✅ SubagentIntegration 并行执行（Implementer + Reviewer 并行）
- ✅ Subagent registry（5 种 Agent: specifier/implementer/reviewer/security/test）
- ✅ verification pipeline（ACT 后自动 security scan + pytest + mypy）
- ✅ MCP bridge + presets + connection lifecycle
- ✅ RalphLoopExecutor 6层统一初始化（WAL/Checkpoint/SelfEvo/ModelRouter/Subagents/TDD）
- ✅ Nexus TUI（Rich 实时仪表盘，可运行）
- ✅ CLI 重构（Click 模块化）— 19/19 测试全通过
- ✅ 类型注解清零 — `src/` 目录 0 mypy errors
- ✅ TUI undo 命令实现

**代码量**
- 60+ Python 文件
- ~21,350 行代码
- 9 个模块包

### ✅ 已完成 (2026-05-05)

**功能完善**
- [x] Model Router — 根据任务复杂度自动选模型
- [x] Checkpoint 恢复 — 失败后自动从检查点恢复
- [x] Self-Evolution — 错误监控+模式捕获+技能库

**TUI 交互**
- [x] Approval/Reject 暂停等待用户输入

**工具链**
- [x] 工具定义统一 — TOOL_DEFINITIONS 统一导出
- [x] bash subprocess 安全 — 移除 shell=True + shlex.split

**代码质量**
- [x] 异常处理改进 — 无 `except: pass`

### 📋 待优化 (非阻塞)

- [ ] agent_loop.typo 巨型函数拆分
- [ ] 更多端到端测试场景

---

### 📊 测试结果 (2026-05-04)

**任务**：创建 Flask REST API（GET/POST/DELETE /todos）

| 工具 | 结果 | 代码行数 | 状态 |
|------|------|----------|------|
| **Nexus** | ✅ 成功 | 57行 | 通过 |
| **Claude Code** | ✅ 成功 | 49行 | 通过 |

**验证结果**：
- GET /todos → `[]` ✅
- POST /todos (title="Test task") → `{"id":1,"title":"Test task","completed":false}` ✅
- DELETE /todos/1 → `{"message":"Todo 1 deleted"}` ✅
- GET /todos → `[]` ✅

**结论**：Nexus 和 Claude Code 都能成功完成相同的 REST API 开发任务。Nexus 采用了更结构化的 RalphLoop 状态机流程，Claude Code 采用直接对话式。

---

## License

MIT
