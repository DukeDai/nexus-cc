# Nexus — 下一代自主编程智能体

一个基于 **RalphLoop 状态机** 的自进化编程智能体，通过显式状态转换+自省机制，超越 Claude Code 的隐式推理可靠性。

## 核心创新

| 特性 | Claude Code | Nexus |
|------|------------|-------|
| TDD 强制 | 建议 | **每次提交前必须** |
| 自学系统 | 项目级记忆 | **跨会话技能库** |
| 上下文模型 | 隐式 | **4-tier 显式** |
| 安全扫描 | 可选 | **每次提交前强制** |
| MCP 集成 | 基础 | **完整+预设库** |
| 交互 TUI | 无 | **Rich 实时仪表盘** |
| CLAUDE.md 层级 | 无 | **三层合并** |
| Hook 系统 | PreTool/PostTool | **6 事件完整** |
| 会话持久化 | 基础 | **SQLite 完整状态** |

## 安装

```bash
cd ~/dev/nexus
pip install -e .
```

## CLI 命令

```bash
# RalphLoop 任务执行（核心）
nexus run [--task TASK] [--tui] [--max-context N]

# 会话管理（跨会话恢复）
nexus session list
nexus session resume <session-id>
nexus session delete <session-id>

# MCP 服务器管理
nexus mcp list
nexus mcp add <name> <command> [env...]
nexus mcp remove <name>
nexus mcp presets          # 查看预设服务器

# 交互式 TUI（实时仪表盘）
nexus tui

# Git Worktree 支持
nexus worktree list
nexus worktree create <branch> [directory]
nexus worktree remove <branch>

# Hook 管理
nexus hooks list
nexus hooks add <event> <hook-script>
```

## 架构

```
Nexus
├── RalphLoop              # 状态机编排引擎
│   ├── states.py          # 8 状态枚举
│   ├── transitions.py     # 转换表 + 守卫条件
│   └── orchestrator.py    # 主引擎 + 恢复
├── agents/                # 多智能体专业化
│   ├── specifier.py       # 需求 → 规格
│   ├── implementer.py      # TDD 强制执行
│   ├── reviewer.py         # 质量门
│   └── security.py        # 安全扫描
├── verification/         # 提交前验证管道
│   ├── tdd_gate.py        # 测试先行门
│   ├── security_scan.py   # 密钥/注入/路径遍历
│   ├── test_gate.py       # 基线对比
│   └── review_gate.py     # 独立审查
├── skills/               # 自改进系统
│   ├── capture.py        # 错误自动捕获
│   ├── author.py         # 技能自动创作
│   └── loader.py         # 任务前技能加载
├── context/              # 上下文管理
│   ├── monitor.py        # 4-tier 预算监控
│   ├── claudemd.py       # CLAUDE.md 三层合并
│   ├── checkpoint.py     # 状态检查点
│   └── worktree.py       # Git Worktree 管理
├── hooks/                # 事件钩子系统
│   ├── hook_manager.py   # 钩子注册与分发
│   ├── pre_tool_hook.py  # 工具执行前
│   └── post_tool_hook.py # 工具执行后
├── mcp/                  # MCP 服务器集成
│   ├── connection.py     # 异步生命周期管理
│   ├── bridge.py         # 工具桥 + 缓存 + 限流
│   ├── config.py         # YAML/JSON 配置
│   ├── integration.py    # RalphLoop 桥接
│   └── presets.py        # GitHub/Slack/PostgreSQL 预设
├── tui/                  # 交互式终端 UI
│   ├── app.py            # Rich Live 主应用
│   ├── state_view.py     # 状态机可视化
│   ├── context_view.py   # 预算仪表盘
│   ├── agent_view.py     # 智能体状态
│   └── task_view.py      # 任务队列
└── session/              # 会话持久化
    ├── models.py         # SessionData 数据模型
    ├── store.py          # SQLite 存储
    └── manager.py        # 会话恢复管理
```

## RalphLoop 状态机

```
  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐
  │  PLAN   │───▶│   ACT   │───▶│ VERIFY  │───▶│  REFLECT │
  └────┬────┘    └────┬────┘    └────┬────┘    └────┬─────┘
       │              │              │              │
       ▼              ▼              ▼              ▼
  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
  │ ESCALATE│    │  COMMIT │    │  ABORT  │    │  TRANS  │
  └─────────┘    └─────────┘    └─────────┘    └─────────┘
```

**状态**: PLAN → ACT → VERIFY → REFLECT → (TRANSIT) → PLAN...
**升级选项**: (1) 自行修复 (2) 求助同事 (3) 简化需求 (4) 放弃任务

## 上下文预算模型

| 层级 | 预算消耗 | 行为 |
|------|---------|------|
| PEAK | 0-30% | 全速执行复杂推理 |
| GOOD | 30-50% | 正常执行 |
| DEGRADING | 50-70% | 减少探索，聚焦已知路径 |
| POOR | 70-100% | 紧急：检查点保存，建议升级 |

## 文件统计

- **102 个文件**
- **15,211 行代码**
- **9 个模块包**

## License

MIT
