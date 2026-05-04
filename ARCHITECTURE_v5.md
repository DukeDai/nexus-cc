# Nexus v5 Architecture — RalphLoop 终极形态

> **版本**：v5（重建版）
> **日期**：2026-05-04
> **目标**：超越 Claude Code/OpenCode/Cline 的下一代 coding AI agent

---

## 背景：为什么需要 v5

### 当前架构的问题

| 问题 | 严重度 | 根因 |
|------|--------|------|
| `nexus.py` 和 `nexus_core.py` 两个入口点功能混乱 | P0 | 架构设计时没有统一规划 |
| RalphLoop orchestrator 从未被真正调用 | P0 | cmd_run() 里只有桩代码 |
| Session persistence 是空壳 | P1 | Checkpoint 未实现 |
| WAL Protocol 完全缺失 | P1 | 状态变更无日志，截断无法恢复 |
| Working Buffer 不存在 | P1 | 60% 上下文后无记录 |
| Tool call 无流式输出 | P1 | Claude Code 实时显示执行，Nexus 同步阻塞 |
| Smart Model Router 无智能 | P2 | 有框架无决策逻辑 |
| 自进化技能未集成 | P2 | 框架有但 nexus 不调用 |
| Git 高级功能缺失 | P2 | 只有基础 git 工具 |
| `nexus.py` flat import 破坏包结构 | P0 | `from ralphloop import X` 不走相对导入 |

### v4 架构的真正优势（必须保留）

```
Claude Code 的问题（我们必须超越）：
  ❌ 无 TDD 强制（代码写完才补测试）
  ❌ 无显式状态可见性（黑盒推理）
  ❌ 无多 Agent 协作（单 agent 打天下）
  ❌ 无自进化能力（每次错误重复犯）
  ❌ 无上下文预算感知（超窗口才知道）

Nexus v4 的创新（必须保留到 v5）：
  ✅ RalphLoop 状态机（PLAN→ACT→VERIFY→REFLECT 全流程）
  ✅ TDD 强制门（RED→GREEN→REFACTOR）
  ✅ 多 Agent 协作（Specifier/Implementer/Reviewer/Security 并行）
  ✅ 4-tier 上下文预算监控
  ✅ CLAUDE.md 三层合并
  ✅ Verification Pipeline
```

---

## 核心原则

```
1. RalphLoop 是心脏：所有行为都通过状态机驱动
2. WAL 在前：每个状态变更前写预写日志
3. Working Buffer 是保险：60% 上下文后记录每个交换
4. TDD 是纪律：不可跳过的 RED→GREEN→REFACTOR
5. Subagent 是加速器：同任务多 Agent 并行
6. Smart Router 是省钱机器：小任务用小模型
7. Self-Evolution 是终极目标：错误不再重复犯
```

---

## v5 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Nexus CLI                                  │
│                     (统一的 entry point)                             │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
        ┌──────────────▼──────────────┐
        │     RalphLoop Engine        │
        │  ┌──────────────────────┐  │
        │  │ WAL Protocol Layer    │  │ ← 每个状态变更前写日志
        │  │ Working Buffer Layer │  │ ← 60% 上下文后记录
        │  └──────────────────────┘  │
        │         │                   │
        │         ▼                   │
        │  ┌──────────────────────┐  │
        │  │   State Machine      │  │
        │  │ IDLE→PLAN→ACT→      │  │
        │  │ VERIFY→REFLECT→    │  │
        │  │ COMMIT/RETRY/ESC/   │  │
        │  │ ABORT               │  │
        │  └──────────────────────┘  │
        └──────────────┬──────────────┘
                       │
     ┌─────────────────┼─────────────────┐
     │                 │                  │
     ▼                 ▼                  ▼
┌─────────┐    ┌──────────────┐    ┌─────────────┐
│ Subagent│    │   LLM Core   │    │  Tools Core │
│  Pool   │    │              │    │             │
│(并行)   │    │ Smart Router │    │ Streaming   │
│Spec/   │    │ + Fallback   │    │ Executor    │
│Impl/   │    │ + Cost Track  │    │ + Safety    │
│Revu/   │    └──────────────┘    └─────────────┘
│Sec/    │
│Test    │
└─────────┘
     │
     ▼
┌──────────────┐    ┌──────────────┐    ┌─────────────┐
│   Session    │    │   Skills     │    │    TUI      │
│  (SQLite)   │    │ (Evolution)  │    │ (Streaming) │
│Checkpoint   │    │Error→Pattern │    │ RalphLoop   │
│ + WAL       │    │→Skill        │    │ State Viz   │
└──────────────┘    └──────────────┘    └─────────────┘
```

---

## 模块设计

### 1. RalphLoop Engine（核心）

**文件**：`src/ralphloop/`

#### 1.1 WAL Protocol Layer

```python
# src/ralphloop/wal.py

class WALProtocol:
    """预写日志协议 — 每个状态变更前必须写日志。
    
    WAL 顺序：
    1. 状态变更请求到达
    2. 写入 WAL 条目（格式：TS + from_state + to_state + context_hash）
    3. 执行状态变更
    4. WAL 条目标记 committed
    
    为什么：即使 context 截断，WAL 文件里有完整的状态变更历史。
    """
    
    def write(self, entry: WALEntry) -> None:
        """写入预写日志条目（同步）"""
        
    def flush(self) -> None:
        """强制刷新到磁盘"""
        
    def recover(self) -> list[WALEntry]:
        """从 WAL 恢复未完成的操作"""
        
    def truncate(self, up_to: str) -> None:
        """截断 WAL 到指定条目（已 committed 的）"""
```

**WAL Entry 格式**：
```
2026-05-04T12:00:00.000001 | IDLE→PLAN | task="Build REST API" | context_hash=abc123 | committed=True
2026-05-04T12:00:01.500002 | PLAN→ACT  | steps=[5 items]      | context_hash=def456 | committed=True
2026-05-04T12:00:05.000003 | ACT→VERIFY| tool_calls=12        | context_hash=ghi789 | committed=False
```

#### 1.2 Working Buffer Layer

```python
# src/ralphloop/working_buffer.py

class WorkingBuffer:
    """工作缓冲区 — 60% 上下文后记录每个交换。
    
    触发条件：context_usage >= 60%
    存储位置：~/.nexus/sessions/{session_id}/working-buffer.md
    
    缓冲区格式：
    ## [timestamp] Human
    [原始消息]
    
    ## [timestamp] Agent (summary)
    [1-2 句总结 + 关键决策]
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.active = False
        self.entries: list[BufferEntry] = []
        
    def activate(self) -> None:
        """60% 上下文时激活"""
        
    def record(self, human_msg: str, agent_summary: str) -> None:
        """记录一个交换"""
        
    def recover(self) -> str:
        """从缓冲区恢复上下文"""
```

#### 1.3 State Machine（重构）

```python
# src/ralphloop/states.py

class RalphState(Enum):
    """RalphLoop 状态枚举"""
    IDLE       = "IDLE"       # 等待用户输入
    PLAN       = "PLAN"       # 分析任务，拆解步骤
    ACT        = "ACT"        # LLM 调用，执行工具
    VERIFY     = "VERIFY"     # TDD Gate / Security / Test
    REFLECT    = "REFLECT"    # 错误模式捕获，决策
    ESCALATE   = "ESCALATE"   # 升级处理
    COMMIT     = "COMMIT"     # 提交更改
    ABORT      = "ABORT"      # 放弃任务
    TRANSIT    = "TRANSIT"    # 状态间过渡
```

**状态转换表**：

| 当前状态 | 触发条件 | 目标状态 | 守卫条件 |
|---------|---------|---------|---------|
| IDLE | user_input | PLAN | valid_task |
| PLAN | plan_ready | ACT | steps > 0 |
| PLAN | plan_failed(3x) | ESCALATE | max_retries |
| ACT | tool_calls_done | VERIFY | has_result |
| ACT | timeout | REFLECT | — |
| VERIFY | all_passed | REFLECT | — |
| VERIFY | failed | ACT (retry) | retries < 3 |
| VERIFY | failed(3x) | ESCALATE | — |
| REFLECT | success | COMMIT | — |
| REFLECT | fixable | ACT | — |
| REFLECT | unfixable | ESCALATE | — |
| REFLECT | abandoned | ABORT | — |
| ESCALATE | user_choice_1 | ACT | self_fix |
| ESCALATE | user_choice_2 | IDLE | ask_help |
| ESCALATE | user_choice_3 | PLAN | simplify |
| ESCALATE | user_choice_4 | ABORT | give_up |

---

### 2. Multi-Agent System（并行化）

#### 2.1 Agent Pool

```python
# src/agents/pool.py

class AgentPool:
    """Agent 连接池 — 支持同任务多 Agent 并行。
    
    并行策略：
    - PLAN 阶段：SpecifierAgent（串行，先理解需求）
    - ACT 阶段：ImplementerAgent（主）+ ReviewerAgent（并行）+ SecurityAgent（并行）+ TestAgent（并行）
    - VERIFY 阶段：所有 Agent 结果汇总
    """
    
    async def run_parallel(
        self,
        agents: list[BaseAgent],
        context: dict
    ) -> list[AgentResult]:
        """并行运行多个 Agent"""
        
    def run_sequential(
        self,
        agents: list[BaseAgent],
        context: dict
    ) -> list[AgentResult]:
        """串行运行多个 Agent"""
```

#### 2.2 Agent 定义

| Agent | 角色 | 并行策略 | 输入 | 输出 |
|-------|------|---------|------|------|
| **Specifier** | 需求分析师 | 串行（最先） | 用户任务 | SPEC.md |
| **Implementer** | 代码生成器 | 主 Agent | SPEC + 上下文 | 代码 + 工具调用 |
| **Reviewer** | 质量审查 | 并行 | 代码 + 测试 | Review 报告 |
| **Security** | 安全扫描 | 并行 | 代码 | 漏洞报告 |
| **Test** | 测试生成 | 并行 | SPEC | RED 测试代码 |

#### 2.3 Subagent Integration

```python
# src/ralphloop/subagent_integration.py（重构）

class SubagentIntegration:
    """Orchestrator ↔ delegate_task 桥接。
    
    职责：
    1. 管理 delegate_task 生命周期
    2. 聚合多 Subagent 结果
    3. 处理超时和错误
    4. 提供流式进度反馈
    """
    
    async def orchestrate_with_subagents(
        self,
        task: str,
        role: AgentRole,
        context: ProjectContext
    ) -> OrchestratedResult:
        """使用 delegate_task 执行 Subagent"""
        
    def _aggregate_results(
        self,
        results: list[SubagentResult]
    ) -> OrchestratedResult:
        """聚合多个 Subagent 的结果"""
```

---

### 3. LLM Core（智能路由）

#### 3.1 Smart Model Router

```python
# src/llm/router.py

class ModelRouter:
    """智能模型路由 — 根据任务复杂度选择最优模型。
    
    决策矩阵：
    | 任务类型        | 复杂度 | 推荐模型            | 理由              |
    |---------------|--------|-------------------|------------------|
    | 简单文件操作    | LOW    | sonnet-4-lite     | 便宜、快速         |
    | 代码审查        | MEDIUM | sonnet-4          | 平衡速度和质量     |
    | 复杂重构        | HIGH   | opus-4            | 深度推理           |
    | 安全扫描        | HIGH   | opus-4            | 全面分析           |
    | TDD 测试生成   | MEDIUM | sonnet-4          | 平衡               |
    | 架构设计        | HIGH   | opus-4            | 复杂推理           |
    
    成本追踪：
    - 每次请求记录 token 消耗
    - 每周报告成本分布
    - 超出预算时自动降级
    """
    
    def route(self, task: str, context: dict) -> str:
        """选择最优模型"""
        
    def track_cost(self, model: str, tokens: int) -> None:
        """追踪成本"""
        
    def get_cost_report(self) -> CostReport:
        """生成成本报告"""
```

#### 3.2 LLM Client（重构）

```python
# src/llm/client.py

class LLMClient:
    """统一 LLM 客户端 — 支持 Anthropic/OpenAI/Ollama。
    
    流式支持：
    - tool_call_use_name: 实时显示工具名
    - tool_call_use_input: 实时显示参数
    - content_block: 实时显示输出
    
    重试策略：
    - 指数退避：1s, 2s, 4s, 8s
    - 最大 3 次重试
    - 特定错误码跳过（rate limit）
    """
    
    async def stream_generate(
        self,
        messages: list[Message],
        model: str,
        tools: list[Tool],
        callback: callable
    ) -> AsyncIterator[StreamEvent]:
        """流式生成 — 实时回调每个事件"""
```

---

### 4. Tools Core（流式执行）

#### 4.1 Streaming Tool Executor

```python
# src/tools/streaming_executor.py

class StreamingExecutor:
    """流式工具执行器 — 实时显示执行过程。
    
    Claude Code 的关键体验：
    - 工具调用时立即显示「正在执行 X」
    - 输出实时流式显示（不是等结束再显示）
    - 错误时显示完整 traceback
    
    实现：
    - 使用 subprocess + PTY 实现伪终端
    - 每个工具输出单独的颜色通道
    - 超时保护 + 资源限制
    """
    
    async def execute_streaming(
        self,
        tool_call: ToolCall,
        output_callback: callable,
        timeout: int = 30
    ) -> ToolResult:
        """流式执行工具"""
        
    def _setup_pty(self, cmd: list[str]) -> pty.PtyProcess:
        """设置伪终端"""
        
    def _stream_output(self, fd: int, callback: callable) -> None:
        """流式输出"""
```

#### 4.2 Tool Definitions

| 工具 | 行为 | 安全限制 |
|------|------|---------|
| **Bash** | 执行 shell 命令 | 危险命令黑名单，超时保护 |
| **Read** | 读取文件 | 仅项目目录内 |
| **write_file** | 写入文件 | 仅项目目录内，自动备份 |
| **Edit** | apply_diff 智能编辑 | hunk 级别应用 |
| **Glob** | 文件搜索 | 仅项目目录内 |
| **Grep** | 内容搜索 | 仅项目目录内 |
| **WebSearch** | 网络搜索 | 仅 informational |
| **Git** | Git 操作 | 危险操作二次确认 |

---

### 5. Session System（持久化）

#### 5.1 Checkpoint（核心）

```python
# src/session/checkpoint.py

class CheckpointManager:
    """检查点管理器 — SQLite 持久化 RalphLoop 状态。
    
    检查点内容：
    - RalphLoop 当前状态 + 上下文
    - 所有 Agent 的中间结果
    - LLM conversation history
    - Tool call 历史
    - TDD 循环进度
    - 上下文预算消耗
    
    触发时机：
    - 每次状态转换后
    - 每次 tool_call 完成后
    - 60% 上下文时
    
    恢复流程：
    1. 从 SQLite 加载最新检查点
    2. 从 WAL 重放未 committed 的操作
    3. 从 Working Buffer 恢复近期交换
    """
    
    def save(self, state: RalphLoopState) -> str:
        """保存检查点，返回 checkpoint_id"""
        
    def load(self, checkpoint_id: str) -> RalphLoopState:
        """加载检查点"""
        
    def list(self) -> list[CheckpointMetadata]:
        """列出所有检查点"""
        
    def recover(self, checkpoint_id: str) -> RalphLoopState:
        """完整恢复"""
```

#### 5.2 Session Store

```python
# src/session/store.py

class SessionStore:
    """会话存储 — SQLite。
    
    表结构：
    - sessions: session_id, created_at, updated_at, status, metadata
    - checkpoints: id, session_id, state, timestamp
    - wal_entries: id, session_id, entry_data, committed
    - cost_tracking: id, session_id, model, tokens, cost, timestamp
    """
    
    def create_session(self, project_path: str) -> str:
        """创建新会话"""
        
    def save_checkpoint(self, session_id: str, state: dict) -> str:
        """保存检查点"""
        
    def load_latest(self, session_id: str) -> dict | None:
        """加载最新检查点"""
```

---

### 6. Skills System（自进化）

#### 6.1 Self-Evolution Engine

```python
# src/skills/evolution.py

class SkillEvolution:
    """技能自进化引擎 — 错误 → 模式 → 技能。
    
    进化流程：
    1. REFLECT 阶段检测到错误模式
    2. 分析根因（systematic-debugging 方法）
    3. 提取解决模式
    4. 生成技能文档
    5. 下次遇到相同模式时自动应用
    
    技能格式：
    ---
    name: fix-{error-type}
    trigger: {error pattern regex}
    solution: {step by step}
   验证: {test case}
    ---
    """
    
    def detect_pattern(self, error: Error) -> str | None:
        """检测错误模式"""
        
    def generate_skill(self, pattern: str, solution: str) -> Skill:
        """生成技能"""
        
    def apply_skill(self, error: Error) -> Skill | None:
        """应用匹配的技能"""
        
    def get_skill(self, name: str) -> Skill | None:
        """获取技能"""
```

---

### 7. TUI（流式可视化）

#### 7.1 Streaming TUI

```python
# src/tui/streaming_app.py

class StreamingTUI:
    """流式终端 UI — 实时显示 RalphLoop 执行过程。
    
    Claude Code 的 UX：
    - ANSI 彩色输出
    - 打字效果（streaming）
    - 进度条和状态指示
    - 实时工具输出
    - 错误高亮显示
    
    Nexus v5 的增强：
    - RalphLoop 状态转换实时显示
    - Subagent 并行执行可视化
    - 上下文预算实时监控
    - 技能自动应用提示
    """
    
    def render_state(self, state: RalphState) -> None:
        """渲染当前状态"""
        
    def stream_output(self, text: str, channel: str) -> None:
        """流式输出文本（打字效果）"""
        
    def show_progress(self, current: int, total: int) -> None:
        """显示进度条"""
        
    def render_agents(self, agents: list[AgentStatus]) -> None:
        """渲染多 Agent 状态"""
```

---

## CLI 入口点（统一）

### `nexus.py` → 废弃

```python
# nexus.py 废弃原因：
# 1. flat import from ralphloop 不走相对导入
# 2. cmd_run() 从未真正调用 orchestrator
# 3. 状态机逻辑完全是桩代码
# → 重写为 nexus_core.py 的单一入口
```

### `nexus_core.py`（重构为唯一入口）

```bash
nexus run --task "Build REST API with FastAPI"
nexus tui
nexus session list
nexus session resume <session-id>
nexus mcp list
nexus hooks add pre-commit ./scripts/security-check.sh
nexus skills list
nexus cost report
```

---

## 执行计划

### Phase 0: 架构修复（Foundation）

| 任务 | 内容 | 依赖 |
|------|------|------|
| **P0.1** | 删除 nexus.py，重写 nexus_core.py 为单一入口 | — |
| **P0.2** | 修复所有 flat import → 正确的 src/ 相对导入 | P0.1 |
| **P0.3** | 连接 cmd_run() → RalphLoop orchestrator（真正调用） | P0.1 |
| **P0.4** | act-e2e：用 nexus_core.py 完成端到端任务 | P0.3 |
| **P0.5** | 所有现有测试通过 + 新增 P0 相关测试 | P0.4 |

### Phase 1: WAL Protocol + Session Persistence

| 任务 | 内容 | 依赖 |
|------|------|------|
| **P1.1** | WAL Protocol 实现（预写日志） | P0.4 |
| **P1.2** | Working Buffer 实现（60% 上下文后记录） | P1.1 |
| **P1.3** | Checkpoint Manager 实现（SQLite 持久化） | P1.1 |
| **P1.4** | Compaction Recovery（从 buffer + WAL 恢复） | P1.2 + P1.3 |
| **P1.5** | Session list/resume 命令完整实现 | P1.3 |

### Phase 2: Claude Code UX 对齐

| 任务 | 内容 | 依赖 |
|------|------|------|
| **P2.1** | Streaming Tool Executor（PTY + 流式输出） | P0.4 |
| **P2.2** | Streaming TUI（打字效果 + 实时状态） | P2.1 |
| **P2.3** | Smart Model Router（智能路由 + 成本追踪） | P0.4 |
| **P2.4** | Git 高级功能（git add -p, branch 管理） | P0.4 |

### Phase 3: 超越 Claude Code

| 任务 | 内容 | 依赖 |
|------|------|------|
| **P3.1** | Self-Evolution Engine（错误→技能） | P1.4 |
| **P3.2** | Subagent 并行化（Agent Pool） | P2.2 |
| **P3.3** | Proactive Behavior（主动建议） | P3.1 |
| **P3.4** | verify-gap：10 个任务与 Claude Code 对比 | P3.2 |

---

## 验收标准

### Phase 0 验收

```
✅ nexus_core.py --task "Create hello.txt with hello world" 完整执行
✅ RalphLoop 状态机真正被调用（PLAN→ACT→VERIFY→REFLECT 全流程）
✅ 所有测试通过（7/7 原有 + 新增 P0 测试）
✅ 无 flat import 错误
```

### Phase 1 验收

```
✅ WAL 条目在每个状态变更前写入
✅ 60% 上下文后 Working Buffer 激活
✅ session resume 能完整恢复状态
✅ Compaction Recovery 测试通过
```

### Phase 2 验收

```
✅ Tool call 流式输出实时显示
✅ TUI 显示打字效果
✅ Smart Router 成本报告生成
✅ git add -p 分块暂存正常工作
```

### Phase 3 验收

```
✅ 相同错误第二次出现时自动应用技能
✅ Subagent 并行执行（加速 > 2x）
✅ 主动建议被用户接受 > 30%
✅ 同等任务完成率 ≥ Claude Code
✅ TDD 覆盖率 > 80%（Claude Code = 0%）
```

---

## 技术债务清理

### 必须删除

```
SPEC_v2.md.bak      # 已合并到 SPEC.md
SPEC_v3.md.bak      # 已合并到 SPEC.md
hello.txt           # 测试残留文件
src/api/main.py     # 孤立的占位符
nexus.py            # 重写的入口点
```

### 必须重写

```
nexus_core.py       # 完整 CLI + 真正调用 orchestrator
src/session/checkpoint.py  # 空实现 → 完整实现
src/tools/streaming_executor.py  # 新建
src/llm/router.py   # 智能路由实现
src/skills/evolution.py  # 自进化引擎
src/tui/streaming_app.py  # 流式 TUI
```

---

## 成功指标

| 指标 | Claude Code 基线 | Nexus v5 目标 |
|------|-----------------|--------------|
| 端到端任务完成率 | ~85% | > 85% |
| TDD 覆盖率 | 0% | > 80% |
| 自进化技能数 | 0 | > 20 |
| 多 Agent 并行任务占比 | N/A | > 50% |
| 上下文效率 | 基线 | > 150% |
| 流式输出延迟 | < 100ms | < 200ms |
| 跨会话恢复成功率 | N/A | > 95% |
| Proactive 建议接受率 | N/A | > 30% |

---

*v5 Architecture — 2026-05-04*
*Nexus: RalphLoop + WAL Protocol + Subagent Parallel + TDD Enforcement + Self-Evolution*
