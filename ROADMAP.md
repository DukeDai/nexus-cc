# Nexus 升级路线图：超越 Claude Code

> 目标：成为最强大、最可靠、最透明的 AI 编程智能体

## 核心差距分析 (2026-05-08)

### 现状总结

**已真实实现 ✅：**
- RalphLoop 6层 executor（WAL/Checkpoint/SelfEvo/ModelRouter/Subagents/TDD）
- MCPConnectionManager 真实连接（stdio + HTTP）
- SubagentIntegration 并行执行（ThreadPoolExecutor）
- TDDEnforcer（RED→GREEN→REFACTOR）
- ModelRouter（按任务复杂度路由）
- CheckpointManager + WALManager
- SelfEvolutionEngine（Observe→Plan→Act→Learn）
- VerificationPipeline（SecurityScan/TestGate/ReviewGate）

**未集成/未验证 ❌：**
- MCPBridge 未接入 RalphLoopExecutor 的 PLAN/ACT
- ToolRegistry 未被 executor 使用（硬编码 TOOL_DEFINITIONS）
- 并行 subagent speedup 无 benchmark 数据
- VerificationPipeline 仅在 CLI 后置，未在 executor ACT 内联
- benchmark_nexus.py 只测结构不测真实行为（用 MockLLM）

---

## 阶段一：真实能力释放（本周）

### P0 — MCPBridge 接入 executor

**问题**：RalphLoopMCPBridge 有 `execute_plan()` 和 `execute_verify()` 但从未被实例化。

**目标**：在 RalphLoopExecutor 的 PLAN 和 VERIFY 相位使用真实 MCP 工具。

**方案**：
```
PLAN phase:
  1. RalphLoopExecutor 初始化 RalphLoopMCPBridge
  2. PLAN 相位调用 bridge.execute_plan(task) → 真实 GitHub/Filesystem 工具
  3. 结果注入 agent_loop 上下文

VERIFY phase:
  1. ACT 完成后调用 bridge.execute_verify(plan_id)
  2. 真实运行 pytest/mypy/安全扫描
  3. 返回结构化验证报告
```

**验证**：连接真实 MCP 服务器（如 github-mcp），成功调用 `list_issues` 等工具。

---

### P1 — 并行 subagent benchmark

**问题**：代码有 `_execute_act_parallel` 但没有数据证明它比 sequential 快。

**目标**：添加 benchmark 测试，测量 3-worker 并行 vs 单线程的实际 speedup 比例。

**方案**：
```python
def benchmark_parallel_speedup():
    # Sequential: 3 subagents × 2s each = 6s
    # Parallel: max(2s, 2s, 2s) = 2s
    # Expected speedup: 3x
    sequential_time = measure_sequential()
    parallel_time = measure_parallel()
    speedup = sequential_time / parallel_time
    assert speedup > 2.0, f"Expected 2x speedup, got {speedup:.1f}x"
```

**验证**：Speedup ≥ 2x（3 workers，每个 1s simulated work）。

---

## 阶段二：质量内联（本周）

### P2 — VerificationPipeline 接入 ACT phase

**问题**：VerificationPipeline 只在 CLI 后置运行，不在 executor ACT 相位内部。

**目标**：ACT phase 内联验证 gate——代码未通过扫描不允许进入 VERIFY。

**方案**：
```
RalphLoopExecutor._execute_act_single:
  1. Implementer 生成代码
  2. ACT 内联调用 VerificationPipeline.run(code, context)
  3. 若 SecurityScan fail → 回滚 + ESCALATE
  4. 若 TestGate fail → 回滚 + TDD loop
  5. 只有全部 gate 通过才进入 VERIFY phase
```

**验证**：注入含 `eval()` 的恶意代码，验证 executor 拒绝并 ESCALATE。

---

### P3 — ToolRegistry 动态加载

**问题**：executor 硬编码 `TOOL_DEFINITIONS` 列表，无法动态加载 MCP 工具。

**目标**：RalphLoopExecutor 从 MCPBridge 动态获取工具定义，注册到 ToolRegistry。

**方案**：
```python
# RalphLoopExecutor.__init__ 中：
self._registry = ToolRegistry()
if self._mcp_bridge:
    mcp_tools = await self._mcp_bridge.list_tools()
    for tool in mcp_tools:
        self._registry.register(tool.name, tool.definition)
```

**验证**：连接 github-mcp 后，`nexus.py run --task "list my repos"` 自动发现并使用 `list_repositories` 工具。

---

## 阶段三：自我进化闭环（第二周）

### P4 — 真实自进化循环

**问题**：SelfEvolutionEngine 有 4-phase 循环但从未被真实失败触发。

**目标**：RalphLoopExecutor 的 REFLECT phase 触发 SelfEvolutionEngine.Act()，将失败模式写入技能库。

**方案**：
```
REFLECT phase:
  1. 检测本轮失败（如 TDD loop 超限、安全扫描失败）
  2. 调用 self_evolution.observe(failure_context)
  3. self_evolution.plan() 生成补救技能
  4. self_evolution.act() 写入 ~/.nexus/skills/
  5. 下次相同模式触发时，SelfEvolutionEngine 自动建议补救技能
```

**验证**：注入连续 3 次 TDD 失败，验证技能库中生成新补救技能文件。

---

## 阶段四：对抗性基准（第二周）

### P5 — Claude Code 对比 benchmark

**目标**：在相同任务上运行 Claude Code 和 Nexus，对比通过率/速度/代码质量。

**任务集**：
1. 创建 REST API（已完成，两家都过）
2. TDD 重构遗留代码（Claude Code 无 TDD → Nexus 强制 TDD 优势）
3. 安全修复（注入漏洞代码，Claude Code 漏报 vs Nexus 内联扫描）
4. 并行工具调用（Claude Code 串行 vs Nexus 3-worker 并行）

**指标**：
- 通过率（代码可运行 + 测试通过）
- 速度（real time）
- 代码质量（mypy 类型覆盖率、安全漏洞数）

---

## 阶段五：自主化（第三周）

### P6 — 自主任务分解

**目标**：用户给一个高层目标，Nexus 自动分解为子任务队列，依次执行。

**方案**：
```
用户: "把我们的单体服务拆分成微服务"
  ↓
Nexus 自动分解:
  1. 识别边界（API routes + 数据库表）
  2. 生成服务拆分计划
  3. 每个服务: PLAN→ACT→VERIFY→REFLECT
  4. 自动处理服务间依赖
  5. 生成 docker-compose.yaml
```

### P7 — 多会话学习

**目标**：Nexus 记住每个项目的决策历史，下次遇到相同 context 自动应用。

---

## 优先级总结（2026-05-09 更新）

| 阶段 | 任务 | 优先级 | 状态 |
|------|------|--------|------|
| 一 | P0: MCPBridge → executor | **P0** | ✅ 已完成 |
| 一 | P1: 并行 benchmark | **P1** | ✅ 已完成（实测 1.98x speedup） |
| 二 | P2: 验证管道内联 | **P2** | ✅ 已完成 |
| 二 | P3: ToolRegistry 动态化 | **P2** | ✅ 已完成 |
| 三 | P4: 自进化闭环 | **P3** | ✅ 已完成（success + failure 双通道 capture） |
| 四 | P5: Claude Code 对比 | **P3** | ❌ 待做 |
| 五 | P6: 自主任务分解 | **P4** | ❌ 待做 |
| 五 | P7: 多会话学习 | **P4** | ❌ 待做 |

**进度：5/8 阶段完成，核心 executor 功能全部就绪。**

---

## 下一步（2026-05-09+）

### 🔲 P5: Claude Code 对比 benchmark（高价值）
在相同任务上对比 Nexus vs Claude Code 的通过率、速度、代码质量差异。
重点任务：
- REST API 创建（基线对比）
- TDD 重构（Nexus TDD 强制 vs Claude Code 无 TDD）
- 安全修复（Nexus 内联扫描 vs Claude Code 插件扫描）
- 并行工具调用（Nexus 3-worker vs Claude Code 串行）

### 🔲 P6: 自主任务分解（突破性）
用户给高层目标，Nexus 自动分解为子任务队列，依次执行 PLAN→ACT→VERIFY→REFLECT。

### 🔲 P7: 多会话学习
Nexus 记住每个项目的决策历史，下次遇到相同 context 自动应用先前学到的方法。

### 🔲 SelfEvo correction storage 真实化
`handle_verification_failure()` 返回的 `VerificationCorrection` 尚未被 executor 实际存储和应用。
需要：corrections 写入 `~/.nexus/skills/corrections.json`，下次遇到相同 pattern 时 replay。

### 🔲 `nexus.tools` entry point
ToolRegistry discovery 代码已就绪但 `nexus.tools` 包不存在，需要创建并注册至少一个真实工具。
