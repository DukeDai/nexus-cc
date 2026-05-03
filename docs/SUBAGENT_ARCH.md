# RalphLoop Subagent 集成架构

## 设计原则

Claude Code 的根本问题：**单 agent 黑盒**。
- 优点：简单、延迟低
- 缺点：无法并行、无法专业化、无法反思

RalphLoop 的创新：**RalphLoop Orchestrator（大脑）+ 专业化 Subagent（手）**

```
┌─────────────────────────────────────────────────────┐
│                   RalphLoop Orchestrator              │
│                  (状态机 + 决策中心)                   │
│                                                      │
│   PLAN ──→ ACT ──→ VERIFY ──→ REFLECT ──→ COMMIT   │
│              ↑                                        │
│    ┌─────────┴─────────────────────────────────┐     │
│    │  delegate_task (subagent pool)            │     │
│    │                                          │     │
│    │  [SpecifierAgent]    需求分解 + SPEC 生成  │     │
│    │  [ImplementerAgent] 代码生成 + TDD 执行    │     │
│    │  [ReviewerAgent]   代码审查 + 质量报告    │     │
│    │  [SecurityAgent]   安全扫描 + 漏洞报告   │     │
│    │  [TestAgent]       测试生成 + 覆盖率分析  │     │
│    └────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## 角色定义

### RalphLoop Orchestrator（主 agent）
- **职责**：状态转换、任务分发、结果聚合、决策
- **特点**：不写代码，只编排
- **LLM 调用**：高阶规划用 Sonnet/M2

### SpecifierAgent（subagent）
- **输入**：用户原始需求
- **输出**：SPEC.md 草案 + 任务分解
- **使用工具**：read_file（理解现有代码）、write_file（写 SPEC）
- **模型**：小模型（MiniMax-M2）省钱

### ImplementerAgent（subagent）
- **输入**：SPEC.md + 实现上下文
- **输出**：代码 + TDD 测试结果
- **使用工具**：read_file/write_file/apply_diff/bash(pytest)
- **模型**：高配（Claude Sonnet）

### ReviewerAgent（subagent）
- **输入**：生成的代码
- **输出**：Review 报告（问题列表 + 建议）
- **使用工具**：read_file、grep（代码搜索）
- **模型**：中配

### SecurityAgent（subagent）
- **输入**：代码 diff
- **输出**：安全漏洞列表
- **使用工具**：read_file、grep（模式匹配）
- **模型**：小模型 + 规则

## 通信协议

### Orchestrator → Subagent
```python
{
    "task_id": "uuid",
    "role": "implementer",
    "goal": "实现用户登录 API...",
    "context": {
        "spec": "...",      # SPEC.md 内容
        "files": [...],     # 现有相关文件
        "constraints": [...] # 约束（安全、性能等）
    },
    "toolsets": ["terminal", "file"],
    "max_turns": 10
}
```

### Subagent → Orchestrator
```python
{
    "task_id": "uuid",
    "role": "implementer", 
    "status": "complete|error|escalate",
    "result": {
        "files_created": [...],
        "files_modified": [...],
        "test_results": {...},
        "summary": "..."
    },
    "escalate_reason": null  # if status == escalate
}
```

## 实现计划

### Phase 1: Subagent Registry
文件：`src/ralphloop/subagent_registry.py`

```python
SUBAGENT_DEFINITIONS = {
    "specifier": {
        "description": "需求分析 + SPEC 生成",
        "system_prompt": "You are a senior product manager...",
        "toolsets": ["terminal", "file"],
        "model": "auto",  # 智能选择
        "max_turns": 5,
    },
    "implementer": {
        "description": "代码实现 + TDD",
        "system_prompt": "You are an expert Python developer...",
        "toolsets": ["terminal", "file", "web"],
        "model": "claude-sonnet-4",
        "max_turns": 15,
    },
    "reviewer": {...},
    "security": {...},
}
```

### Phase 2: Orchestrator 改造
文件：`src/ralphloop/orchestrator.py`（改造）

```python
class RalphLoop:
    def act_with_subagents(self, task: str) -> ActResult:
        # 1. 启动 ImplementerAgent（代码生成 + TDD）
        impl_future = delegate_task(
            goal=f"实现: {task}\n\n约束: {self.context.constraints}",
            context=self._build_subagent_context(),
            tasks=[{
                "goal": "生成代码并运行 TDD",
                "role": "implementer"
            }]
        )
        
        # 2. 启动 ReviewerAgent（代码审查，并行）
        review_future = delegate_task(
            goal=f"审查代码: {task}",
            context=self._build_subagent_context(),
            tasks=[{
                "goal": "代码审查",
                "role": "reviewer"
            }]
        )
        
        # 3. 等待结果
        impl_result = impl_future.result()
        review_result = review_future.result()
        
        # 4. RalphLoop 决策
        return self._decide(impl_result, review_result)
```

### Phase 3: CLAUDE.md Loader
文件：`src/ralphloop/claude_md_loader.py`

```python
def load_claude_md(workdir: Path) -> str | None:
    """从当前目录向上搜索 CLAUDE.md"""
    current = workdir.resolve()
    for parent in [current] + list(current.parents):
        claudemd = parent / "CLAUDE.md"
        if claudemd.exists():
            return claudemd.read_text()
    return None
```

### Phase 4: Nexus TUI（状态可视化）
文件：`src/tui/nexus_tui.py`

```
┌──────────────────────────────────────────────────┐
│  RalphLoop  ██████████░░░░░░░░░░░░░  45% used   │
├──────────────────────────────────────────────────┤
│  [PLAN]  →  [ACT]  →  [VERIFY]  →  [REFLECT]    │
│             ████████░░░░░░░░░                       │
│             Implementer: running...               │
│             Reviewer: ✓ complete                 │
├──────────────────────────────────────────────────┤
│  Recent:                                         │
│  ✓ Implementer: Created auth.py (3 files)       │
│  ✓ Reviewer: 2 suggestions (low severity)       │
│  → TDD: 3/3 tests passing                      │
└──────────────────────────────────────────────────┘
```

## 与 Claude Code 的关键差异

| 能力 | Claude Code | RalphLoop+Subagent |
|------|------------|---------------------|
| **并行化** | ❌ 单线程 | ✅ Implementer+Reviewer 并行 |
| **专业化** | ❌ 万能 agent | ✅ 角色专业化 |
| **可见性** | ❌ 黑盒 | ✅ 状态机可视化 |
| **TDD** | ❌ 无 | ✅ 内置 |
| **自进化** | ❌ 无 | ✅ 错误→技能 |
| **多模型** | ❌ | ✅ 智能路由 |

## 执行

```bash
# act-subagent-arch
python -c "
from src.ralphloop import RalphLoop
from src.ralphloop.subagent_integration import SubagentIntegration

loop = RalphLoop(workdir=Path('.'))
integration = SubagentIntegration(loop)

# 端到端测试
result = integration.run('实现一个简单的计算器类')
print(result)
"
```

---

*最后更新：2026-05-04*
