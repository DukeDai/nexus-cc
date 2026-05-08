# Nexus — 下一代自主编程智能体

Nexus 是基于 **RalphLoop 状态机** 的 AI 编程智能体，通过显式状态转换 + 多 Agent 协作 + TDD 强制，超越 Claude Code 的隐式推理可靠性。

> RalphLoop = Ralph（北极燕鸥 🦅）+ Loop（闭环）— 像 Ralph 一样快速、持久、目标明确地完成编程任务。

---

## 核心创新

> **状态 (2026-05-08):** 本次审计新增：MCP 真实会话调用（_sessions dict）、VerificationPipeline ACT 内联 gate、ToolRegistry 动态加载、SelfEvo VERIFY 闭环。**59/59 benchmark 全绿。**

| 特性 | Claude Code | Nexus | 状态 |
|------|------------|-------|------|
| TDD 强制 | 建议 | **每次提交前 RED→GREEN→REFACTOR** | ✅ TDDEnforcer 完整实现，prompt-based enforcement |
| 多 Agent 协作 | ❌ 无 | **Specifier/Implementer/Reviewer/Security 并行** | ✅ SubagentIntegration + ThreadPoolExecutor 并行 |
| 状态可见性 | 黑盒 | **RalphLoop PLAN→ACT→VERIFY→REFLECT** | ✅ 6层 executor 真实驱动状态机 |
| 项目感知 | CLAUDE.md | **三层合并（全局/项目/目录）** | ✅ claude_md_loader 实现 |
| 上下文预算 | 隐式 | **4-tier 显式监控 PEAK/GOOD/DEGRADING/POOR** | ✅ context/monitor.py 实现 |
| 安全扫描 | 插件 | **每次提交前强制内置扫描** | ✅ verification/security_scan.py + ACT gates |
| 自进化技能 | ❌ 无 | **错误→模式捕获→技能库** | ✅ SelfEvolutionEngine + WAL crash recovery |
| Subagent 并行 | ❌ 无 | **ThreadPoolExecutor Implementer+Reviewer 并行** | ✅ subagent_integration._execute_act_parallel |
| 会话持久化 | 基础 | **SQLite 检查点恢复** | ✅ CheckpointManager + WALManager |
| MCP 工具桥接 | ❌ 无 | **RalphLoopMCPBridge 接入 PLAN/VERIFY** | ✅ mcp_bridge → PLAN/VERIFY，真实 session.call_tool() |
| 验证内联 Gate | ❌ 无 | **ACT 后立即 SecurityScan (fail-closed)** | ✅ VerificationPipeline 4-stage，SecurityScan 阻断恶意代码 |
| 工具动态发现 | ❌ 无 | **ToolRegistry auto-discover nexus.tools** | ✅ _init_tool_registry() 优先级：registry → custom_tools → TOOL_DEFINITIONS |

---

## 安装

```bash
git clone https://github.com/DukeDai/nexus-cc.git
cd nexus-cc
pip install -e .
pip install readchar>=4.0    # TUI 命令输入支持
```

---

## 配置

### 环境变量

```bash
# 必须设置
export ANTHROPIC_API_KEY="***"    # Anthropic API Key

# 可选配置
export ANTHROPIC_AUTH_TOKEN=***            # 备用认证 Token
export ANTHROPIC_BASE_URL=""               # API 代理/网关地址
export ANTHROPIC_MODEL=""                  # 默认 claude-sonnet-4-20250514
export NEXUS_PROVIDER="anthropic"          # anthropic/openai/ollama
```

---

## 快速开始

```bash
# RalphLoop 任务执行
python nexus.py run --task "Create a REST API with FastAPI"

# 交互式 TUI（实时监控 + 交互命令）
python nexus.py tui -t "Create a REST API with FastAPI" -C /path/to/project
python nexus.py tui                         # 空队列，仅监控

# 会话管理
python nexus.py session list
python nexus.py session resume <session-id>

# MCP / Skills
python nexus.py mcp list
python nexus.py mcp presets
python nexus.py skills list
```

---

## TUI 交互命令

| 命令 | 说明 |
|------|------|
| `status` | 显示当前状态 |
| `help` | 显示帮助 |
| `approve` | 批准当前操作 |
| `reject` | 拒绝当前操作 |
| `retry` | 重试当前任务 |
| `skip` | 跳过当前任务 |
| `quit` / `exit` | 退出 TUI |

**Approval**：DEGRADING 状态或危险命令时暂停，等待 `approve`/`reject`  
**Escalation**：重试超限时按 `1=force-merge 2=rewrite 3=abandon 4=decompose`

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

**流转**：PLAN → ACT → VERIFY → REFLECT → (TRANSIT) → PLAN...  
**升级**：① 自行修复 ② 求助同事 ③ 简化需求 ④ 放弃任务

---

## TDD 强制循环

```
用户: "实现登录功能"
    ↓
┌──────────────────┐
│ 1. RED           │ ← 写测试（预期失败）
└────────┬─────────┘
         ↓
┌──────────────────┐
│ 2. GREEN         │ ← 写实现（最小代码）
└────────┬─────────┘
         ↓
    ┌────┴────┐
    │ PASS?   │
    └────┬────┘
      Y       N
      ↓       ↓
  ┌────────┐ ┌──────────────────┐
  │REFACTOR│ │ DEBUG LOOP(≤3)  │
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
│   ├── orchestrator.typo    # 主引擎
│   ├── agent_loop.typo      # LLM 闭环执行
│   ├── tdd_enforcer.typo    # TDD 强制
│   ├── transitions.typo     # 转换表
│   ├── states.typo          # 8 状态枚举
│   ├── subagent_registry.typo    # 5 专业 Agent
│   ├── subagent_integration.typo  # Orchestrator↔delegate_task 桥接
│   └── claude_md_loader.typo      # CLAUDE.md 三层合并
├── agents/                 # 多智能体专业化
│   ├── specifier.typo       # 需求 → 规格
│   ├── implementer.typo     # TDD 强制执行
│   ├── reviewer.typo        # 质量门
│   └── security.typo        # 安全扫描
├── verification/          # 提交前验证管道
│   ├── tdd_gate.typo        # 测试先行门
│   ├── test_gate.typo       # 基线对比
│   ├── security_scan.typo   # 密钥/注入/路径遍历
│   ├── review_gate.typo     # 独立审查
│   └── pipeline.typo        # 验证管道编排
├── context/               # 上下文管理
│   ├── monitor.typo         # 4-tier 预算监控
│   ├── claudemd.typo        # CLAUDE.md 三层合并
│   ├── checkpoint.typo      # 状态检查点
│   └── worktree.typo        # Git Worktree 管理
├── self_evolution/        # 自进化引擎
│   └── engine.typo          # 错误监控+模式捕获+技能库
├── tui/                    # 交互式终端 UI
│   ├── nexus_tui.typo       # ANSI 实时仪表盘
│   └── app.typo             # Rich Live 主应用
├── mcp/                    # MCP 服务器集成
│   ├── client.typo          # 异步生命周期
│   ├── bridge.typo          # 工具桥 + 缓存 + 限流
│   └── presets.typo         # GitHub/Slack/PostgreSQL 预设
└── llm/
    ├── client.typo          # Anthropic/OpenAI/Ollama 统一
    └── model_router.typo    # 根据复杂度自动选模型
```

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
project/CLAUDE.typo        ← 项目规范（架构决策、约定）
directory/.CLAUDE.typo     ← 目录规范（模块规则、local overrides）
        ↓
    build_llm_system_prompt()
        ↓
    注入 LLM System Prompt
```

---

## ✅ 已完成功能

**核心架构**
- [x] RalphLoop 状态机 + orchestrator
- [x] LLM-driven agent_loop（真正调用 LLM + 工具闭环）
- [x] TDD Enforcer 完整集成（RED→GREEN→REFACTOR）
- [x] CLAUDE.md loader（三层合并）
- [x] SubagentIntegration 并行执行（Implementer + Reviewer ThreadPoolExecutor 并行）
- [x] Subagent registry（5 种 Agent）
- [x] verification pipeline（ACT 后自动 security scan + pytest + mypy）
- [x] MCP bridge + presets + connection lifecycle
- [x] RalphLoopExecutor 6层统一初始化（WAL/Checkpoint/SelfEvo/ModelRouter/Subagents/TDD）
- [x] Nexus TUI（Rich 实时仪表盘）
- [x] CLI 重构（Click 模块化）— 38/38 测试全通过（19 CLI + 19 executor 集成）
- [x] Model Router — 根据任务复杂度自动选模型
- [x] Checkpoint 恢复 — 失败后自动从检查点恢复
- [x] Self-Evolution — 错误监控+模式捕获+技能库
- [x] Approval/Reject 暂停等待用户输入
- [x] 工具定义统一 — TOOL_DEFINITIONS 统一导出
- [x] bash subprocess 安全 — 移除 shell=True + shlex.split
- [x] 异常处理改进 — 无 `except: pass`
- [x] WAL crash recovery — WALManager 日志化 + 恢复计划生成

**本次审计修复 (2026-05-07)**
- [x] benchmark_nexus.py 导入修复（nexus_root 路径 + `from enum import member` 错误）
- [x] run.py RalphLoopExecutor 从简化版替换为真正 6 层 executor
- [x] WAL → run_agent_loop 真实 wiring（+ wal=self._wal 参数）
- [x] ModelRouter 修复（select_model 返回 str 而非 ModelConfig）
- [x] SubagentIntegration 修复（run_agent_Loop → run_agent_loop typo）
- [x] venv 重建（Python 3.9 → 3.12 + pytest/mypy 安装）
- [x] README 核心创新表格更新（诚实状态标注）

## ✅ 已完成功能
- [x] RalphLoopExecutor 6层统一初始化（WAL/Checkpoint/SelfEvo/ModelRouter/Subagents/TDD）
- [x] Model Router — 根据任务复杂度自动选模型
- [x] Checkpoint 恢复 — 失败后自动从检查点恢复
- [x] Self-Evolution — 错误监控+模式捕获+技能库
- [x] SubagentIntegration run_specifier/run_security_scan 真实调用
- [x] agent_loop 巨型函数拆分（_apply_diff 141行 → 4个<60行子函数）

---

## 📊 测试对比 (2026-05-04)

**任务**：创建 Flask REST API（GET/POST/DELETE /todos）

| 工具 | 结果 | 代码行数 |
|------|------|----------|
| **Nexus** | ✅ 成功 | 57行 |
| **Claude Code** | ✅ 成功 | 49行 |

**验证**：GET/POST/DELETE 全部通过

---

## 统计数据

- **60+** Python 文件
- **~21,350** 行代码
- **9** 个模块包
- **38/38** 测试全通过（19 CLI + 19 executor 集成）
- **mypy**: src/ 0 errors

---

## License

MIT