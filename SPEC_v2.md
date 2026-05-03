# Nexus v2 — RalphLoop 重构设计
# RalphLoop Iteration: 从骨架到真正引擎

## 当前状态 (v1 问题清单)

| 问题 | 严重性 | 说明 |
|------|--------|------|
| **无 LLM 集成** | 🔴 致命 | agents 只是返回 mock result，没有真正调用 LLM |
| **无工具执行** | 🔴 致命 | 没有 Bash/Git/File/Search 工具，RalphLoop 无法真正执行 |
| **agent_executor 是空壳** | 🔴 致命 | `cmd_run` 里的 executor 返回 hardcoded mock result |
| **无端到端执行** | 🔴 致命 | 无法在真实代码库上运行任务 |
| **MCP 是单向配置** | 🟡 中 | MCP 服务器配置了但没有连接/调用机制 |
| **TDD gate 是 mock** | 🟡 中 | TDD 流程存在但没有真正运行测试 |
| **无 diff/patch 能力** | 🟡 中 | Claude Code 的核心能力是智能 apply_changes，Nexus 没有 |

---

## 竞争分析：Claude Code 的真正架构

Claude Code 不是"一个 agent"，它是一个 **tool-augmented LLM loop**：

```
User Input
    ↓
[CLAUDE.md loaded] ← 项目上下文
    ↓
Claude Sonnet/Opus/Haiku ← 模型选择
    ↓
Tool Use Protocol ← bash, edit, glob, grep, write, read...
    ↓
Loop (直到 task 完成 或 max_turns)
    ↓
Git commit (可选)
```

**Claude Code 的核心能力**：
1. **智能模型路由** — 复杂任务用 Opus，简单任务用 Haiku
2. **Tool use protocol** — 结构化工具调用，LLM 输出 `tool_use` JSON
3. **Diff 应用引擎** — `apply_changes` 是 Claude Code 最强大的能力之一
4. **MCP 工具桥接** — 连接外部工具服务器
5. **CLAUDE.md 上下文** — 项目级记忆

---

## v2 目标架构

### 核心层

```
Nexus v2
├── llm/                      # LLM 集成层
│   ├── client.py            # 多 Provider 客户端 (Anthropic/OpenAI/Ollama)
│   ├── tool_call.py         # Tool use protocol 实现
│   └── model_router.py      # 任务复杂度 → 模型选择
├── tools/                   # 工具集
│   ├── bash.py              # Shell 执行 (with timeout, cwd)
│   ├── editor.py            # 文件编辑 (apply_changes/diff)
│   ├── glob.py              # 文件查找
│   ├── grep.py              # 内容搜索
│   ├── read.py              # 读取文件
│   ├── write.py             # 写入文件
│   ├── git.py               # Git 操作
│   └── web_search.py        # 网页搜索
├── engine/                   # RalphLoop 执行引擎
│   ├── executor.py          # 真正的 agent 执行循环
│   ├── loop.py              # 主循环 (PLAN→ACT→VERIFY→REFLECT)
│   └── context.py           # 上下文管理
├── mcp/                      # MCP 客户端
│   ├── client.py            # MCP stdio/HTTP 客户端
│   └── tool_bridge.py       # MCP ↔ Nexus 工具桥接
└── ralphloop/               # 状态机 (已存在，保持)
```

### 执行流程 (真正的 RalphLoop)

```
nexus run "Add user auth"
    ↓
[1] LOAD CLAUDE.md + Skills
    ↓
[2] PLAN: LLM 分析任务，输出执行计划
    ↓
[3] LOOP (max_turns=100):
    │
    ├─→ [3a] ACT: LLM generates tool_calls
    │       ToolExecutor.run(tool_calls)
    │       → Bash / Edit / Glob / Grep / Git...
    │
    ├─→ [3b] VERIFY: LLM reviews output
    │       security_scan.run()
    │       tdd_gate.run() if applicable
    │
    └─→ [3c] REFLECT: Check if task done
            Yes → COMMIT
            No → continue to [3a]
    ↓
[4] COMMIT: git add + commit + push
```

---

## 实现优先级

### Phase 1: LLM 集成 (最优先)

```python
# llm/client.py — 多 Provider LLM 客户端
class LLMClient:
    def __init__(self, provider: Provider):
        self.provider = provider  # anthropic | openai | ollama
    
    def complete(self, messages: list[Message], tools: list[Tool]) -> Response:
        """Send completion request with tool definitions."""
        
    def complete_streaming(self, messages, tools, callback):
        """Streaming response for TUI."""
```

### Phase 2: 工具系统 (最优先)

```python
# tools/bash.py — 受控 shell 执行
class BashTool(BaseTool):
    def execute(self, command: str, timeout: int = 30, cwd: str = None) -> ToolResult:
        """执行命令，超时保护，输出截断。"""
        
# tools/editor.py — 智能 diff 应用
class EditorTool(BaseTool):
    def apply_changes(self, file_path: str, changes: list[Change]) -> ToolResult:
        """Claude Code 的核心能力：智能 apply edits。"""
        # 解析 diff 格式
        # 精确行号应用
        # 冲突检测
        
    def apply_diff(self, file_path: str, diff: str) -> ToolResult:
        """应用 unified diff。"""
```

### Phase 3: 执行引擎

```python
# engine/executor.py — RalphLoop 的心脏
class RalphExecutor:
    def __init__(self, llm_client, tools, hooks):
        self.llm = llm_client
        self.tools = tools
        self.hooks = hooks
    
    def run_loop(self, task: str, max_turns: int = 100) -> LoopResult:
        """真正的执行循环。"""
        messages = [SystemMessage(...)]
        for turn in range(max_turns):
            response = self.llm.complete(messages, tools=self.tools.definitions)
            for tool_call in response.tool_calls:
                result = self.tools.execute(tool_call)
                messages.append(ToolResult(tool_call.id, result))
            if response.is_complete:
                break
        return LoopResult(messages=messages)
```

### Phase 4: MCP 客户端

```python
# mcp/client.py — 真正的 MCP 客户端
class MCPClient:
    async def connect(self, command: list[str], env: dict) -> None:
        """启动 MCP 服务器进程。"""
        
    async def list_tools(self) -> list[Tool]:
        """列出 MCP 服务器提供的工具。"""
        
    async def call_tool(self, name: str, args: dict) -> dict:
        """调用 MCP 工具。"""
```

---

## 关键设计决策

### 1. Tool Use Protocol vs Function Calling

Claude Code 用的是 **Tool Use protocol**（Anthropic 官方），不是 OpenAI 的 function calling。
Nexus 应该支持两种：

```python
# 对话级别：Anthropic Tool Use
{"type": "tool_use", "name": "Bash", "input": {"command": "ls", "timeout": 30}}

# API 兼容：OpenAI function calling  
{"type": "function", "function": {"name": "Bash", ...}, "arguments": "{...}"}
```

### 2. Diff/Apply Changes 是核心能力

Claude Code 的 `apply_changes` 是用户最喜欢的功能：

```
用户说 "change function foo to bar"
    ↓
LLM 分析文件，找到 foo
    ↓
生成最小 diff
    ↓
apply_changes(file, changes)
    ↓
精确应用编辑，不破坏其他代码
```

实现要点：
- 解析 `<<<<<<< HEAD` / `=======` / `>>>>>>>` 冲突标记
- 精确行号定位
- 多文件编辑支持

### 3. 安全边界

Bash 工具必须：
- `timeout` 强制，防止无限循环
- `allowed_commands` 白名单（可选）
- `dangerous_commands` 黑名单（`rm -rf /`, `dd`, `:(){:|:&};:`）
- 输出截断（max 10KB）

### 4. 模型路由（真正的 D2）

```python
def route_model(task_complexity: str, budget: ContextTier) -> str:
    if task_complexity == "trivial" and budget <= ContextTier.GOOD:
        return "claude-3-haiku-20240229"
    elif task_complexity == "normal" and budget <= ContextTier.DEGRADING:
        return "claude-3-5-sonnet-20241022"
    else:
        return "claude-3-5-opus-20241120"
```

---

## 演进目标

| 阶段 | 里程碑 | 验证方式 |
|------|--------|---------|
| v2.0 | LLM + 工具执行跑通 | `nexus run "ls -la"` 成功 |
| v2.1 | 文件编辑跑通 | `nexus run "在 foo.py 添加 bar 函数"` 成功 |
| v2.2 | Git 工作流跑通 | `nexus run "添加功能并 commit"` 成功 |
| v2.3 | TDD 跑通 | 实现功能时先写测试 |
| v2.4 | MCP 集成跑通 | 连接真实 MCP 服务器 |
| v2.5 | 对比测试 | 与 Claude Code 相同的任务，Nexus 也能完成 |
| v3.0 | 超越 | Nexus 完成 Claude Code 做不到的事情 |
