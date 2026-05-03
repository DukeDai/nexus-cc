# RalphLoop 终极目标：超越 Claude Code

## Claude Code 优势（我们必须达到的能力）

### 1. 核心能力
| 能力 | Claude Code | RalphLoop 当前 | Gap |
|------|------------|----------------|-----|
| 文件读写编辑 | ✅ 完善 | ✅ agent_loop.py 基础 | ~90% |
| 项目根检测 + CLAUDE.md | ✅ | ✅ claude_md_loader.py 完成 | 已完成 |
| Bash 工具 | ✅ 完善 | ✅ 基础 | ~85% |
| Glob/Grep | ✅ 完善 | ✅ 基础 | ~90% |
| Git 集成 | ✅ | ⚠️ 基础 | 中等 |
| 安全扫描 | ✅ 内置 | ✅ security_scan.py 完成 | ~80% |
| 上下文管理 | ✅ 智能 | ✅ 4-tier 监控框架完成 | ~70% |
| TTY 交互（连续监控） | ✅ | ✅ Nexus TUI ANSI 仪表盘 | ~80% |
| 多文件 diff | ✅ | ✅ apply_diff | ~90% |
| Tool call 流式输出 | ✅ | ❌ 同步 | 中等 |

### 2. UX/CLI 体验
| 能力 | Claude Code | RalphLoop 当前 | Gap |
|------|------------|----------------|-----|
| 优雅 CLI 输出 | ✅ | ⚠️ Rich app.py 框架在 | 中等 |
| 进度指示 | ✅ | ✅ Nexus TUI 仪表盘 | ~80% |
| 错误恢复 | ✅ 优雅 | ⚠️ 基础 | 中等 |
| ANSI 彩色输出 | ✅ | ✅ Nexus TUI | ~90% |

### 3. 架构优势（Claude Code 没有的创新）
| 创新 | Claude Code | RalphLoop 状态 |
|------|------------|----------------|
| TDD 强制门 | ❌ | ✅ tdd_enforcer.py + agent_loop 联动 |
| 多 Agent 协作 | ❌ | ✅ subagent_registry + SubagentIntegration |
| RalphLoop 状态可见性 | ❌ | ✅ Nexus TUI 实时仪表盘 |
| 4-Tier Context Budget | ❌ | ✅ orchestrator ContextTier 联动 |
| 技能自进化 | ❌ | ❌ 待实现 |
| Subagent 并行化 | ❌ | ✅ delegate_task 集成 |
| Multi-model 路由 | ⚠️ 有限 | ⚠️ 有框架，无智能路由 |
| 跨会话 Checkpoint | ❌ | ❌ 待实现 |
| Skill 系统 | ❌ | ⚠️ 有框架，nexus 无集成 |

---

## 终极目标声明

> **RalphLoop 是在保持 Claude Code 核心体验完整度的基础上，通过 TDD 强制、多 Agent 协作、自进化技能系统、和 RalphLoop 状态可见性，实现一个「会反思、懂改进、能协作」的下一代 coding agent。**

---

## Gap 分析（优先级排序）

### 🔴 P0 — 必须有才能工作
1. **nexus_core.py 真正运行** — 当前存在但不够健壮
2. **CLAUDE.md loader** — 让 agent 理解项目规范
3. **Nexus TUI** — 实时输出，否则无法 debug
4. **真实任务完成** — 一个端到端任务从到到尾

### 🟡 P1 — Claude Code 对齐
5. **优雅 CLI 输出** — 彩色、进度、表格
6. **更完善的安全扫描** — 内置非插件
7. **Git 高级功能** — branch/merge/diff
8. **Tool call 流式** — 实时看到执行过程

### 🟢 P2 — 超越 Claude Code
9. **TDD Enforcement 完整闭环** — 当前框架在但未强制
10. **Multi-Agent 协作** — Specifier/Implementer/Reviewer 分工
11. **自进化技能系统** — 错误 → 技能捕获
12. **RalphLoop UI** — 用户可见状态转换
13. **Subagent 并行化** — 同一任务多 agent 并行
14. **Checkpoint 持久化** — 跨会话恢复
15. **智能 Model 路由** — 小任务用小模型省钱

---

## 执行计划

### 迭代 1（当前 → act-realagents）
**目标：RalphLoop orchestrator 真正调用 subagent**
```
RalphLoop ACT state
  → Subagent 1 (Specifier): 分解任务
  → Subagent 2 (Implementer): 生成代码 + 工具
  → Subagent 3 (Reviewer): 检查质量
  → TDD Enforcer: 验证
  → RalphLoop REFLECT: 判断是否重试/提交/升级
```

### 迭代 2（act-e2e）
**目标：用 Nexus 开发一个真实项目**
```
nexus "Build a REST API with FastAPI"
  → 自动生成 SPEC.md
  → 自动 TDD RED 测试
  → 自动 GREEN 实现
  → 自动 REFACTOR
  → Git commit
```

### 迭代 3（verify-gap）
**目标：在 10 个任务上与 Claude Code 对比**

### 迭代 4（reflect → 自进化）
**目标：自动从错误中生成技能**

---

## Claude Code 核心体验清单（必须对标）

### 文件操作
- [ ] read_file: 行号、语法高亮感知、超过限制截断
- [ ] write_file: 自动创建目录、自动备份（可选）
- [ ] apply_diff: 智能 hunk 应用，失败时优雅降级到 write
- [ ] glob: 支持 **/*.py 等递归模式
- [ ] grep: 支持 `-i`, `-n`, `-C` 等选项

### Bash
- [ ] 超时保护
- [ ] 工作目录隔离
- [ ] 环境变量注入
- [ ] 管道安全（不泄漏 secret）

### 项目感知
- [ ] 自动检测 .git, package.json, pyproject.toml, Cargo.toml
- [ ] CLAUDE.md 加载和合并
- [ ] 根目录自动上升搜索

### Git
- [ ] git add -p（交互式暂存）
- [ ] git commit --amend
- [ ] git stash / stash pop
- [ ] 分支创建和切换

### 安全
- [ ] 禁止 `rm -rf /`
- [ ] 禁止 commit secrets
- [ ] 禁止危险 shell 命令

---

## Nexus 创新清单（Claude Code 没有）

### 1. TDD 强制门
```
用户: "实现登录功能"
  ↓
Nexus: 写 RED test（预期失败）→ pytest → 确认失败
  ↓
Nexus: 写 GREEN impl → pytest → 确认通过
  ↓
Nexus: REFACTOR → pytest → 确认通过
  ↓
Git commit
```

### 2. Multi-Agent 协作
```
用户任务
  ↓
SpecifierAgent → 澄清需求 → 生成 SPEC.md
  ↓
ImplementerAgent → 生成代码 + 工具
  ↓
ReviewerAgent → Code review + 建议
  ↓
SecurityAgent → 漏洞扫描
  ↓
RalphLoop REFLECT → 决策
```

### 3. 自进化技能系统
```
错误发生 → Pattern 捕获 → 技能生成 → 下次避免
```
错误 → 根因分析 → 解决模式 → 存为技能

### 4. RalphLoop 状态可见性
```
[PLAN] ████████████ 80% → [ACT] ░░░░░░░░░░░ 0%
```
用户实时看到 agent 在做什么状态

### 5. Context Budget 感知
```
Context: PEAK (28%) | Tool calls: 12 | Turns: 4
```
不超上下文，超前预警

---

## 成功指标

| 指标 | Claude Code 基线 | RalphLoop 目标 |
|------|-----------------|----------------|
| 端到端任务完成率 | ~85% | > 85% |
| TDD 覆盖率 | 0% | > 80% |
| 自进化技能数 | 0 | > 10 |
| 多 Agent 协作任务 | N/A | > 50% |
| 上下文效率 | 基线 | > 150% |

---

*最后更新：2026-05-04*
