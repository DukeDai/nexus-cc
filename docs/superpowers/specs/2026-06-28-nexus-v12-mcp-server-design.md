# Nexus v1.2 — MCP Server Mode — Design Spec

> **Date**: 2026-06-28
> **Status**: Draft for review
> **Builds on**: v1.1.0 (multi-agent + memory + self-evolution) — `docs/superpowers/specs/2026-06-28-nexus-v11-multi-agent-memory-design.md`
> **Scope**: Nexus v1.2 — expose Nexus as an MCP server so other agents (Claude Code, IDEs, custom tools) can drive plans/sessions via the standard MCP protocol
> **Goal**: 让 Nexus 从 **MCP 消费者** 升级为 **MCP 双向公民** — 任何支持 MCP 的客户端都能启动/观察/控制一个 Nexus 会话
>
> **Deferral notice (2026-06-28):** Deferred from v1.2 to v1.3 (stdio-only transport first); see ROADMAP.md v1.3 section. v1.2 ships Model Router only. Spec retained for design continuity.

---

## 1. 背景与动机

### 1.1 v1.1 现状（截至 2026-06-28）

| 维度 | 数据 |
|---|---|
| MCP 角色 | **仅 client**（`src/mcp/` 整套是 connector/bridge/presets） |
| 入口 | `nexus mcp list` / `nexus mcp presets`（配置 mcporter） |
| Bridge | `RalphLoopMCPBridge` 让 RalphLoop 的 PLAN/VERIFY 阶段调 MCP tool（`github`、`slack`、`postgresql`） |
| 暴露面 | 无；外部 agent 不能 drive Nexus |

Nexus 知道怎么"调用别人"（通过 MCP client + mcporter CLI），但别人不知道怎么"调用 Nexus"。一个 Claude Code 用户想在自己的会话里启动 Nexus sub-plan、或观察 Nexus 正在跑的 plan 进度、或让 CI bot 触发 `nexus run --task "fix flaky test"` — 今天只能走 shell + 文件；没有 protocol 层抽象。

### 1.2 v1.2 目标：让 Nexus 成为 MCP ecosystem 一等公民

| 现状痛点 | v1.2 解法 |
|---|---|
| Claude Code 不能 drive Nexus | 暴露 `nexus_run_plan` / `nexus_session_*` 作为 MCP tools |
| 外部 agent 看不到 Nexus 状态 | 暴露 `nexus://sessions/{id}` / `nexus://sessions/active` 作为 MCP resources |
| 模板化的 plan 启动（"复用一个 auth 模板"） | 暴露 `nexus_plan_template` 作为 MCP prompt |
| CI bot 想远程触发 | SSE/HTTP transport + bearer token auth |
| IDE 想内嵌 Nexus | stdio transport（local child process） |

### 1.3 差异化价值

- **Claude Code ↔ Nexus 互操作**：Claude Code 的 MCP config 加一行 `"nexus": {"command": "nexus", "args": ["mcp", "serve"]}` 即可让 Claude 调用 Nexus 的 plan/session 能力
- **生态复用**：任何 MCP 客户端（Cline、Continue、Zed、Cursor、自建 agent）零成本接入 Nexus
- **复用既有抽象**：tools/resources/prompts 三件套 1:1 映射到 `AgentRuntime` / `SessionManager` / `PlanTemplate` —— 不发明新概念

### 1.4 范围与非目标

**做**：
- `nexus mcp serve` 子命令：std（stdio，default）+ `--http` SSE transport
- 8 tools + 2 resources + 1 prompt（MCP surface，见 §4）
- Bearer token auth（HTTP 模式）
- 进程模型 in-process（reuse 现有 `SessionManager`）
- 文档：README / ARCHITECTURE / ROADMAP / CHANGELOG 增量更新
- 测试：unit + integration + LLM smoke（启动 MCP client → 调 tool → 验证 Plan 生成）

**不做**：
- v1.2 暂不实现 OAuth 2.1 / PKCE（v1.3）
- 暂不实现 streaming progress notifications（v1.3 探索）
- 暂不实现 MCP prompts 模板的多变量组合（v1.3）
- 暂不实现 Nexus-as-MCP-client 调用自己-as-server（环回）— 无意义场景

---

## 2. 核心理念

**Protocol-first, not API-first。** 不发明新的 REST/gRPC；完全遵循 MCP spec（`https://modelcontextprotocol.io`）。这样任何 MCP 兼容客户端零成本接入，且未来 spec 演进（如 MCP Apps、streaming）我们白嫖。

**Process-local by default。** stdio 模式下 Nexus server 与 client 同一进程（或父子进程），共享内存 `SessionManager` 实例 —— 0 网络 hop、0 序列化成本、0 auth 复杂度。HTTP 模式留给跨机场景。

**Tools = verbs, Resources = nouns, Prompts = templates。** 严格遵循 MCP 心智模型：
- **Tools** 改变状态（run/resume/pause/abort）
- **Resources** 暴露状态快照（session 元数据、active session）
- **Prompts** 提供模板化输入（"用 X 任务类型起一个 plan"）

**Idempotency 是契约。** 所有 `nexus_session_*` tools 在重复调用同一 ID 时返回当前状态而非报错；只有 `nexus_run_plan`（创建新 session）要求 task description 必填。这是 MCP tool 语义的核心：客户端 retry 安全。

**Backwards-compatible by construction。** v1.2 是纯加法：所有现有 CLI / TUI / API 不动。`nexus mcp serve` 是新子命令；现有 `nexus mcp list/presets` 行为不变。

---

## 3. 架构总览

### 3.1 进程模型：std vs HTTP

```
=== stdio mode (default, local) ===

Claude Code (parent)
   │
   │ spawn child via stdio
   ▼
nexus mcp serve
   │
   │ reuses in-process SessionManager
   │ reuses in-process AgentRuntime
   │
   ▼
FastMCP server (stdio transport)
   │
   ▼
JSON-RPC over stdin/stdout


=== HTTP mode (remote, --http) ===

CI bot (curl / MCP HTTP client)
   │
   │ HTTPS + Bearer token
   ▼
nexus mcp serve --http --port 8765
   │
   │ auth middleware (token check)
   │
   ▼
FastMCP server (streamable-http transport)
   │
   ▼
in-process SessionManager + AgentRuntime
```

**关键设计决策**：两种 transport 都共用同一个 `NexusMCPServer` 实例 —— transport 只是 wire format 差异，business logic（tool/resource/prompt handlers）只有一份。

### 3.2 模块布局

```
src/mcp/                            # existing — consumer side (unchanged)
├── transport.py                    # mcporter CLI wrappers (existing)
├── integration.py                  # RalphLoopMCPBridge (existing)
├── presets.py                      # GitHub/Slack/Postgres presets (existing)
├── config.py                       # MCPConfigManager (existing)
└── ...

src/mcp_server/                     # NEW — server side
├── __init__.py                     # re-exports
├── app.py                          # NexusMCPServer (FastMCP subclass)
├── tools.py                        # 8 @mcp.tool() handlers
├── resources.py                    # 2 @mcp.resource() handlers
├── prompts.py                      # 1 @mcp.prompt() handler
├── auth.py                         # BearerTokenMiddleware
├── runtime_bridge.py               # glue: MCP ↔ AgentRuntime / SessionManager
└── cli.py                          # `nexus mcp serve` Click command

src/cli/commands/mcp.py             # MODIFIED — add `serve` subcommand
```

### 3.3 顶层数据流（一次 `nexus_run_plan` tool call）

```
MCP client (Claude Code)
   │
   │ JSON-RPC: {"method": "tools/call", "params": {"name": "nexus_run_plan", "arguments": {"task": "...", "role": "implementer"}}}
   ▼
FastMCP server (tools.py)
   │
   │ calls runtime_bridge.run_plan(task, role)
   │
   ▼
runtime_bridge.run_plan
   │ 1. SessionManager.create(description=task)
   │ 2. AgentRuntime.plan(task=task, spec=role.system_prompt if role else None)
   │ 3. session.metadata.status = "planned"
   │ 4. SessionManager.save(...)
   │ 5. return {session_id, plan_summary}
   │
   ▼
JSON-RPC response: {"result": {"content": [{"type": "text", "text": "..."}], "session_id": "abc12345"}}
```

### 3.4 复用现有模块

| MCP tool handler 调用 | 复用模块 |
|---|---|
| `nexus_run_plan` | `src/agent/runtime.py:AgentRuntime.plan` + `src/session/manager.py:SessionManager.create/save` |
| `nexus_session_list` | `src/session/manager.py:SessionManager.list` |
| `nexus_session_resume` | `src/agent/runtime.py:AgentRuntime` + `src/session/manager.py:SessionManager.load/restore` |
| `nexus_session_status` | `src/session/manager.py:SessionManager.get_stats` + per-session metadata |
| `nexus_session_pause` | `src/agent/control.py:ControlChannel.pause` (via runtime reference) |
| `nexus_session_abort` | `src/agent/control.py:ControlChannel.abort` |
| `nexus_role_list` | `src/agents/registry.py:RoleRegistry.list_roles` |
| `nexus_memory_query` | `src/context/memory.py:MemoryStore` (v1.1) |

**关键不变式**：MCP server 不重新实现任何 business logic —— 它是 thin protocol adapter，把 MCP tool calls 转译为既有 API 调用。

### 3.5 v1.0 → v1.2 不变式

| 不变式 | 如何维持 |
|---|---|
| Plan 是 first-class artifact | MCP tool 返回 `session_id` + `plan_summary`（不返回完整 Plan JSON — 留给 `nexus://sessions/{id}` resource） |
| 所有事件走 ControlChannel | MCP server 不直接读 events；它只是触发 command |
| WAL JSONL append-only | 不变 — MCP server 通过 SessionManager 写 WAL |
| 子命令命名 `nexus mcp *` | `serve` 是 mcp group 的新子命令，与 `list`/`presets` 并列 |
| Session ID 8 字符 | 不变 — `new_session_id()` 复用 |

---

## 4. MCP Surface Design

### 4.1 Tools（8 个）

每个 tool 一个 Python function + `@mcp.tool()` 装饰器。Function signature 决定 input schema（MCP SDK 从 type hints 推断）。

#### 4.1.1 `nexus_run_plan`

```python
@mcp.tool(
    name="nexus_run_plan",
    annotations=ToolAnnotations(
        title="Run Nexus plan",
        destructive_hint=False,
        idempotent_hint=False,  # 创建新 session — 非幂等
    ),
)
async def nexus_run_plan(
    task: str,
    role: str | None = None,
    model: str | None = None,
    tags: list[str] | None = None,
    auto_walk: bool = False,
) -> dict[str, Any]:
    """Generate (and optionally execute) a Nexus plan for a task.

    Args:
        task: Natural-language task description (e.g. "add OAuth login to the API").
        role: Optional AgentRole name to constrain the planner (e.g. "implementer", "specifier").
              Maps to RoleDefinition.system_prompt injection (v1.1 feature).
        model: Optional model override (e.g. "claude-sonnet-4-5"). Defaults to env config.
        tags: Optional tags to attach to the new session (for later filtering).
        auto_walk: If True, immediately start walking the plan after generation.
                   If False (default), only generate the plan and return the session_id.

    Returns:
        Dict with:
          - session_id: 8-char ID for the new session
          - plan_id:    Plan identifier (UUID)
          - step_count: Number of steps in the generated plan
          - status:     "planned" if auto_walk=False, "walking" otherwise
          - summary:    Human-readable plan summary (first 3 step descriptions)

    Raises:
        McpError: If LLM call fails or returns invalid plan JSON.
    """
    session_id = session_manager.create(
        description=task,
        model=model,
        tags=tags or [],
    )
    role_def = role_registry.get(role) if role else None
    spec = role_def.system_prompt if role_def else None
    plan = await runtime.plan(task=task, spec=spec)
    session_manager.save(session_id, runtime._walker._ctx, ...)
    if auto_walk:
        asyncio.create_task(runtime.walk(plan))
        status = "walking"
    else:
        status = "planned"
    return {
        "session_id": session_id,
        "plan_id": plan.id,
        "step_count": len(plan.steps),
        "status": status,
        "summary": _summarize_plan(plan, max_steps=3),
    }
```

**为什么 `auto_walk` 默认 False**：默认行为是"先生成 plan，等用户审批" — 这与 v1.0/v1.1 的 plan-first 设计哲学一致。客户端（如 Claude Code）可以读 `nexus://sessions/{session_id}` resource 看到 plan，approve 后再调 `nexus_session_resume(session_id, action="walk")`。

#### 4.1.2 `nexus_session_list`

```python
@mcp.tool(
    name="nexus_session_list",
    annotations=ToolAnnotations(title="List Nexus sessions", read_only_hint=True),
)
def nexus_session_list(
    status: str | None = None,  # "active" | "paused" | "completed" | "failed" | "abandoned"
    limit: int = 20,
    project_path: str | None = None,
) -> dict[str, Any]:
    """List Nexus sessions for the current (or specified) project.

    Args:
        status: Optional filter by session status.
        limit: Maximum number of sessions to return (default 20, max 100).
        project_path: Optional project path filter; defaults to current directory.

    Returns:
        Dict with:
          - sessions: List of session metadata dicts (id, description, status, created_at, etc.)
          - total:    Total matching sessions (may exceed limit)
    """
    sessions = session_manager.list(
        status=SessionStatus(status) if status else None,
        limit=min(limit, 100),
    )
    return {
        "sessions": [s.to_dict() for s in sessions],
        "total": len(sessions),
    }
```

#### 4.1.3 `nexus_session_resume`

```python
@mcp.tool(
    name="nexus_session_resume",
    annotations=ToolAnnotations(title="Resume a Nexus session", idempotent_hint=True),
)
async def nexus_session_resume(
    session_id: str,
    action: str = "walk",  # "walk" | "replan" | "show"
    from_step: int | None = None,
) -> dict[str, Any]:
    """Resume a previously paused/failed Nexus session.

    Args:
        session_id: 8-char session ID.
        action:
          - "walk":   Resume walking the plan from where it stopped.
          - "replan": Discard failed steps and regenerate plan.
          - "show":   Return current state without resuming (read-only).
        from_step: Optional step index to resume from (default = saved cursor).

    Returns:
        Dict with:
          - session_id: Echo
          - status:     Current status after resume attempt
          - action:     Echo
          - cursor:     Current step index

    Raises:
        McpError: If session_id not found or session already completed.
    """
    data = session_manager.load(session_id)
    if data is None:
        raise McpError(f"Session not found: {session_id}")
    if action == "show":
        return {"session_id": session_id, "status": data.metadata.status.value, "cursor": data.ralphloop.task_index}
    ralphloop = session_manager.restore(data)
    if action == "walk":
        # Reuse existing runtime + walker (or create new if process-local state lost)
        asyncio.create_task(runtime.walk())
        return {"session_id": session_id, "status": "walking", "cursor": ralphloop.task_index}
    elif action == "replan":
        plan = await runtime.plan(task=data.metadata.description)
        # ... save new plan to session
```

#### 4.1.4 `nexus_session_status`

```python
@mcp.tool(
    name="nexus_session_status",
    annotations=ToolAnnotations(title="Get session status", read_only_hint=True, idempotent_hint=True),
)
def nexus_session_status(session_id: str) -> dict[str, Any]:
    """Get detailed status of a Nexus session.

    Args:
        session_id: 8-char session ID.

    Returns:
        Dict with full session metadata + RalphLoop snapshot + task queue summary.
        See src/session/models.py:SessionData.to_dict() for full schema.
    """
    data = session_manager.load(session_id)
    if data is None:
        raise McpError(f"Session not found: {session_id}")
    return data.to_dict()
```

#### 4.1.5 `nexus_session_pause`

```python
@mcp.tool(
    name="nexus_session_pause",
    annotations=ToolAnnotations(title="Pause session", idempotent_hint=True),
)
def nexus_session_pause(session_id: str, reason: str = "") -> dict[str, Any]:
    """Pause a running Nexus session at the next step boundary.

    Args:
        session_id: 8-char session ID of an active session.
        reason: Optional human-readable reason (logged).

    Returns:
        Dict with {"session_id": ..., "status": "pausing", "reason": ...}
    """
    runtime = active_runtimes.get(session_id)
    if runtime is None:
        raise McpError(f"No active runtime for session: {session_id}")
    runtime.pause()
    return {"session_id": session_id, "status": "pausing", "reason": reason}
```

#### 4.1.6 `nexus_session_abort`

```python
@mcp.tool(
    name="nexus_session_abort",
    annotations=ToolAnnotations(
        title="Abort session",
        destructive_hint=True,  # 破坏性 — abort 终止 plan
        idempotent_hint=True,
    ),
)
def nexus_session_abort(session_id: str, reason: str = "") -> dict[str, Any]:
    """Abort a running Nexus session immediately.

    Args:
        session_id: 8-char session ID of an active session.
        reason: Optional human-readable reason (logged + recorded in metadata).

    Returns:
        Dict with {"session_id": ..., "status": "aborted", "reason": ...}

    Note:
        If session is already completed/failed/aborted, returns current status (idempotent).
    """
    runtime = active_runtimes.get(session_id)
    if runtime is None:
        # Idempotent: session may already be done
        data = session_manager.load(session_id)
        if data is None:
            raise McpError(f"Session not found: {session_id}")
        return {"session_id": session_id, "status": data.metadata.status.value, "reason": reason}
    runtime.abort(reason=reason)
    return {"session_id": session_id, "status": "aborting", "reason": reason}
```

#### 4.1.7 `nexus_role_list`

```python
@mcp.tool(
    name="nexus_role_list",
    annotations=ToolAnnotations(title="List agent roles", read_only_hint=True, idempotent_hint=True),
)
def nexus_role_list() -> dict[str, Any]:
    """List all registered AgentRoles.

    Returns:
        Dict with:
          - roles: List of role info dicts (name, description, allowed_tools, model_tier, max_subplan_steps)
          - count: Number of roles
    """
    roles = role_registry.list_roles()
    return {
        "roles": [
            {
                "name": r.role.name,
                "description": r.role.description,
                "allowed_tools": r.allowed_tools,
                "model_tier": r.model_tier.value,
                "max_subplan_steps": r.max_subplan_steps,
            }
            for r in roles
        ],
        "count": len(roles),
    }
```

#### 4.1.8 `nexus_memory_query`

```python
@mcp.tool(
    name="nexus_memory_query",
    annotations=ToolAnnotations(title="Query Nexus memory", read_only_hint=True, idempotent_hint=True),
)
def nexus_memory_query(
    query: str,
    kind: str = "episodic",  # "episodic" | "semantic" | "skill"
    k: int = 5,
) -> dict[str, Any]:
    """Query Nexus memory layer (v1.1 feature).

    Args:
        query: Natural-language query.
        kind: Memory layer to search:
              - "episodic": Past plan outcomes (from WAL).
              - "semantic": Project conventions / code chunks (requires embeddings extra).
              - "skill":    Applicable skills from src/skills/.
        k: Max results to return (default 5, max 20).

    Returns:
        Dict with:
          - results: List of memory entries (schema varies by kind)
          - kind:    Echo
          - count:   Number of results returned
    """
    if kind == "episodic":
        entries = memory_store.episodic().similar_past(query, k=k)
    elif kind == "semantic":
        entries = memory_store.semantic().search(query, k=k)
    elif kind == "skill":
        entries = memory_store.skills().suggest(query, plan=None)
    else:
        raise McpError(f"Unknown memory kind: {kind}")
    return {"results": [e.to_dict() for e in entries], "kind": kind, "count": len(entries)}
```

### 4.2 Resources（2 个）

Resources 是 read-only 状态快照。URI 格式：`nexus://sessions/{id}` 或 `nexus://sessions/active`。

#### 4.2.1 `nexus://sessions/{session_id}`（Resource Template）

```python
@mcp.resource("nexus://sessions/{session_id}")
def session_resource(session_id: str) -> str:
    """Expose full session state as a readable resource.

    Returns:
        JSON-encoded SessionData (see src/session/models.py).

    Note:
        MCP resource contents are strings — clients parse JSON.
        For large sessions (>10KB), consider chunking via pagination params in future.
    """
    data = session_manager.load(session_id)
    if data is None:
        raise McpError(f"Session not found: {session_id}")
    return data.to_json()
```

**为什么是 template**：URI pattern `nexus://sessions/{session_id}` 让 MCP client 可以列举所有 sessions（read resource list → 拿到所有 match URI → 读每个）；同时 single read `nexus://sessions/abc12345` 直接拿到那一个。

#### 4.2.2 `nexus://sessions/active`（Static Resource）

```python
@mcp.resource("nexus://sessions/active")
def active_sessions_resource() -> str:
    """List currently active (walking/paused) sessions.

    Returns:
        JSON dict with {"active": [...], "count": N}.
    """
    active = session_manager.list(status=SessionStatus.ACTIVE, limit=100)
    paused = session_manager.list(status=SessionStatus.PAUSED, limit=100)
    combined = active + paused
    return json.dumps({
        "active": [s.to_dict() for s in combined],
        "count": len(combined),
    })
```

### 4.3 Prompts（1 个）

Prompts 提供模板化输入 — MCP client 可以把它们当作"快捷输入"。

```python
@mcp.prompt(name="nexus_plan_template")
def nexus_plan_template(
    task_type: str = "feature",  # "feature" | "bugfix" | "refactor" | "test"
    language: str = "python",
    context: str | None = None,
) -> str:
    """Generate a structured prompt for `nexus_run_plan`.

    Args:
        task_type: Type of work — affects role + tooling heuristics.
        language: Primary programming language (informs planner context).
        context: Optional additional context (code snippets, error logs, etc.).

    Returns:
        A formatted prompt string ready to pass as `task` argument to nexus_run_plan.

    Example (from client):
        prompt = mcp.get_prompt("nexus_plan_template",
                                 task_type="bugfix",
                                 language="python",
                                 context="flaky test: test_foo_x sometimes fails with AssertionError")
        # → user fills in specifics, then calls nexus_run_plan(task=prompt)
    """
    role_map = {
        "feature": "implementer",
        "bugfix": "implementer",
        "refactor": "implementer",
        "test": "specifier",
    }
    role = role_map.get(task_type, "implementer")

    template = f"""[TASK TYPE] {task_type}
[LANGUAGE]   {language}
[ROLE]       {role}
[CONTEXT]
{context or '(none provided)'}

[REQUEST]
Please {task_type} the following in this codebase:
"""
    return template
```

### 4.4 Surface 总览表

| Type | Name | Read/Write | Idempotent | Destructive |
|---|---|---|---|---|
| Tool | `nexus_run_plan` | W | No | No |
| Tool | `nexus_session_list` | R | Yes | No |
| Tool | `nexus_session_resume` | W | Yes | No |
| Tool | `nexus_session_status` | R | Yes | No |
| Tool | `nexus_session_pause` | W | Yes | No |
| Tool | `nexus_session_abort` | W | Yes | **Yes** |
| Tool | `nexus_role_list` | R | Yes | No |
| Tool | `nexus_memory_query` | R | Yes | No |
| Resource | `nexus://sessions/{session_id}` | R | Yes | No |
| Resource | `nexus://sessions/active` | R | Yes | No |
| Prompt | `nexus_plan_template` | R | Yes | No |

---

## 5. Transport

### 5.1 stdio（default，local）

```python
# src/mcp_server/app.py

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="nexus",
    instructions=(
        "Nexus MCP server exposes plan/session operations as tools, "
        "active sessions as resources, and plan templates as prompts. "
        "Default workflow: nexus_run_plan → review nexus://sessions/{id} → "
        "nexus_session_resume(action='walk')."
    ),
)

# Register all tools/resources/prompts (via import for side effects)
from . import tools, resources, prompts  # noqa: F401, E402

if __name__ == "__main__":
    mcp.run()  # stdio transport by default
```

```bash
# CLI entry point
nexus mcp serve
# or with explicit transport
nexus mcp serve --transport stdio
```

**Client config example**（Claude Code 的 `.mcp.json`）：

```json
{
  "mcpServers": {
    "nexus": {
      "command": "nexus",
      "args": ["mcp", "serve"],
      "env": {
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
        "NEXUS_PROJECT": "/path/to/project"
      }
    }
  }
}
```

### 5.2 HTTP（remote，--http）

```bash
nexus mcp serve --http --host 0.0.0.0 --port 8765 --token "${NEXUS_MCP_TOKEN}"
```

```python
# src/mcp_server/cli.py

@click.command("serve")
@click.option("--transport", default="stdio", type=click.Choice(["stdio", "http"]))
@click.option("--http", "use_http", is_flag=True, help="Use HTTP transport (alias for --transport=http)")
@click.option("--host", default="127.0.0.1", help="HTTP host (only with --http)")
@click.option("--port", default=8765, type=int, help="HTTP port (only with --http)")
@click.option("--token", default=None, help="Bearer token for HTTP auth (required with --http)")
def serve(transport: str, use_http: bool, host: str, port: int, token: str | None) -> None:
    """Run Nexus as an MCP server."""
    if use_http:
        transport = "http"
    if transport == "http":
        if not token:
            raise click.UsageError("--token required with --http")
        from .app import mcp
        from .auth import BearerTokenMiddleware
        mcp.add_middleware(BearerTokenMiddleware(token=token))
        mcp.run(transport="streamable-http", host=host, port=port)
    else:
        from .app import mcp
        mcp.run(transport="stdio")
```

**Client config example**（远程 CI bot）：

```json
{
  "mcpServers": {
    "nexus-remote": {
      "url": "https://nexus.example.com:8765/mcp",
      "headers": {
        "Authorization": "Bearer ${NEXUS_MCP_TOKEN}"
      }
    }
  }
}
```

### 5.3 Transport 决策矩阵

| 场景 | Transport | Auth |
|---|---|---|
| Claude Code 本地驱动 | stdio | None（OS 进程隔离） |
| Cursor / Cline IDE | stdio | None |
| CI bot 远程触发 | HTTP + Bearer | 静态 token（env var） |
| 多用户 web 服务 | HTTP + OAuth 2.1 | OAuth（v1.3 — 超出本文档） |
| 同一机器多 Nexus 实例 | stdio + 不同 project path | None |

---

## 6. 并发模型

### 6.1 核心约束

Nexus 一次只能 walk 一个 plan（per AgentRuntime 实例 — see `src/agent/runtime.py`）。但 MCP client 可以同时发多个 tool call。

### 6.2 策略：serialize + reject on conflict

| Scenario | Behavior |
|---|---|
| `nexus_run_plan` called while another plan walking | **Reject** with `McpError("Another plan is walking: {session_id}. Use nexus_session_pause/abort first.")`. MCP client should retry after pause/abort. |
| `nexus_session_resume(action="walk")` while another walking | **Reject** same as above. |
| `nexus_run_plan` called while paused session exists | **Allow** — generates new plan for new session_id. |
| `nexus_session_status/list` while plan walking | **Allow** — read-only, no conflict. |
| `nexus_session_pause/abort` on non-existent session_id | **Idempotent return** — current status (no error). |
| `nexus_session_resume` on completed session | **Reject** with `McpError("Session already completed; use action='show'")`. |
| Multiple MCP clients connected to same HTTP server | Shared `AgentRuntime` + `SessionManager` — first walker wins, others queue/reject per above rules. |
| 同一 client 发并发 tool calls | FastMCP handles serialization per tool; safe. |

### 6.3 实现：runtime registry

```python
# src/mcp_server/runtime_bridge.py

_active_runtimes: dict[str, AgentRuntime] = {}
_walking_lock = asyncio.Lock()  # only one plan walking at a time


async def run_plan(task: str, role: str | None, ...) -> dict[str, Any]:
    async with _walking_lock:  # serialize at server level
        # Check if any runtime currently walking
        for sid, rt in _active_runtimes.items():
            if rt._walker and not rt._walker.is_idle:
                raise McpError(f"Another plan walking: {sid}")
        # ... proceed to create + plan + walk
```

### 6.4 跨 transport 一致性

stdio 和 HTTP 模式共享 `_active_runtimes` dict —— 同一 server 实例下，两种 transport 看到的活动 session 状态一致。这避免了"stdio 跑了 plan，HTTP client 看不到"的诡异情况。

---

## 7. 认证

### 7.1 stdio：无认证

**Why**：stdio transport 下，server 与 client 是父子进程。OS 进程边界即安全边界。攻击者拿到你的 MCP server stdout 权限 ≈ 拿到你的用户权限 —— 这是已 accept 的信任模型（所有 MCP stdio server 都这样）。

### 7.2 HTTP：Bearer token middleware

```python
# src/mcp_server/auth.py

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Validates Authorization: Bearer <token> on every HTTP request.

    Exempts /healthz for liveness probes.
    """

    def __init__(self, app, *, token: str):
        super().__init__(app)
        self._expected = token

    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "missing_token", "error_description": "Authorization: Bearer <token> required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="nexus-mcp"'},
            )

        token = auth_header.removeprefix("Bearer ").strip()
        # Constant-time comparison (defense against timing attacks)
        if not hmac.compare_digest(token, self._expected):
            return JSONResponse(
                {"error": "invalid_token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        return await call_next(request)
```

**Why constant-time compare**：`hmac.compare_digest` 防 timing attack — 即使 token 错误，response time 一致。

### 7.3 不实现的内容（v1.3+）

- OAuth 2.1 + PKCE：MCP spec 推荐；v1.3 计划
- Token rotation：v1.3 计划
- Per-user authorization（一个 token 不能看别人的 session）：v1.3 计划
- mTLS：超出 MCP spec 范围

### 7.4 安全注意事项

- Token 通过 CLI flag 传入 → shell history 风险。文档建议：`export NEXUS_MCP_TOKEN=...; nexus mcp serve --token "$NEXUS_MCP_TOKEN"`
- 默认 `--host 127.0.0.1`（不绑 0.0.0.0）— 防止意外暴露
- README 警告：HTTP 模式**绝不**在公网裸跑

---

## 8. 向后兼容

### 8.1 不变行为

- 现有 CLI 命令（`nexus run`、`nexus tui`、`nexus session *`、`nexus mcp list/presets`）行为完全不变
- 现有 TUI panels / keybindings 不动
- 现有 MCP client 代码（`src/mcp/transport.py` 等 consumer 侧模块）零修改
- WAL 格式不变（v1.0/v1.1/v1.2 全部兼容）
- 现有 test suite 全部 pass

### 8.2 增量改动清单

| 文件 | 改动类型 | 兼容性 |
|---|---|---|
| `src/mcp_server/` (new dir) | NEW | N/A |
| `src/cli/commands/mcp.py` | ADD `serve` subcommand to existing group | additive |
| `src/cli/main.py` | 不动（mcp group 已注册） | unchanged |
| `pyproject.toml` | ADD `mcp[cli]>=1.12` dependency | additive |
| README / ARCHITECTURE / ROADMAP | ADD v1.2 section | additive |
| CHANGELOG.md | ADD v1.2 entry | additive |

### 8.3 升级路径

- 现有用户：`pip install -e .` 不变；MCP server 可选启用（`pip install -e .[mcp-server]` 或默认）
- 客户端集成：可选；现有 Claude Code 用户不集成 Nexus MCP server 也完全无感

### 8.4 依赖策略

```toml
# pyproject.toml additions (v1.2)
[project.optional-dependencies]
mcp-server = ["mcp[cli]>=1.12.0"]

# OR — default install (decision pending; recommend optional for size):
# dependencies = [..., "mcp[cli]>=1.12.0", ...]
```

**推荐**：optional extra。原因：MCP SDK 体积不小；不启用 server 的用户（如纯 CLI/TUI 用户）不应被动拉取。

---

## 9. 失败模式

| Failure | Detection | Recovery |
|---|---|---|
| MCP client 断连（stdio） | EOF on stdin | Server shuts down gracefully (SIGTERM handler) |
| MCP client 断连（HTTP） | TCP disconnect | Server keeps running (other clients unaffected); cleanup on next request |
| LLM call fails during `nexus_run_plan` | Exception in `runtime.plan()` | Return `McpError(llm_error_message)`; session marked failed in DB |
| LLM call timeout | `asyncio.wait_for(runtime.plan(), timeout=120s)` | Return `McpError("Planner timeout after 120s")` |
| Invalid plan JSON from LLM | Parser raises | Return `McpError("Planner returned invalid plan JSON")`; session marked failed |
| Session_id 不存在 | `session_manager.load()` returns None | Return `McpError("Session not found: {id}")` |
| Bearer token 缺失（HTTP） | Middleware | Return 401 with `WWW-Authenticate` header |
| Bearer token 错误（HTTP） | Middleware | Return 401 |
| Bearer token 泄漏（log 里） | Sanitize in logging middleware | Never log token; mask in error messages |
| 并发 plan 冲突 | `_walking_lock` contention | Return `McpError("Another plan walking: {sid}")` |
| 进程崩溃 mid-walk | Process restart + WAL replay | `nexus mcp serve` 不自动恢复（设计）；下次启动时 `nexus_session_resume` 显式调用 |
| Disk full during session save | OSError in `session_manager.save()` | Return `McpError("Disk full")`; plan in memory but not persisted (logged) |
| Network partition during HTTP transport | Starlette handles | Return 503 to client |
| Malformed tool arguments from client | Pydantic validation in FastMCP | Return validation error to client (MCP standard) |
| FastMCP SDK upgrade breaking changes | SemVer pin + smoke tests | Pin `mcp[cli]>=1.12,<2.0` until migration plan ready |
| Old client (pre-2025-03 spec) connects | Spec negotiation | FastMCP handles; log warning if major version mismatch |
| TUI 同时操作同一 session | TUI 监听 WAL changes | 最后写者赢（last-writer-wins on conflicting state） |

---

## 10. 测试策略

### 10.1 Coverage targets

| Layer | Tests | Coverage target |
|---|---|---|
| Unit — `runtime_bridge.py` glue logic | 12 tests: session creation, plan invocation, pause/abort, error mapping | 90% line coverage |
| Unit — `auth.py` BearerTokenMiddleware | 6 tests: missing token, wrong token, valid token, timing attack resistance (constant-time), /healthz exemption | 100% branch coverage |
| Unit — tool handlers (mocked dependencies) | 16 tests: 2 per tool (happy + error) | 85% per tool |
| Unit — resource handlers | 4 tests: session lookup, active listing, missing session, JSON encoding | 90% |
| Unit — prompt handler | 2 tests: default params, custom params | 100% |
| Integration — FastMCP in-process client | 4 tests: stdio transport roundtrip (start server, connect client, call tool, assert result); HTTP transport roundtrip; concurrent tool calls serialize correctly; bearer auth middleware end-to-end | Each scenario: start server → connect MCP client → call tools → verify state changes |
| Integration — nexus_run_plan end-to-end | 2 tests: real LLM call generates valid Plan + session persisted; auto_walk=True actually starts walker | Each scenario: assertion on session DB + WAL contents |
| LLM smoke — real Anthropic API | 2 tests: nexus_run_plan + nexus_session_status flow; nexus://sessions/{id} resource readable via MCP client | Skipped without `ANTHROPIC_API_KEY` |
| Backward compat — run all v1.0 + v1.1 tests | 1 CI step | All 107 + ~35 v1.1 = ~142 tests still pass |
| Client SDK compat — `mcp` Python client connects | 1 smoke test: client SDK can list tools, call `nexus_run_plan`, read `nexus://sessions/active` | Verified in CI |

### 10.2 Total test target

142 (v1.1 baseline) + ~25 new MCP server = **~167 tests**.

### 10.3 v1.2 release criteria (Definition of Done)

1. All 167 tests pass; coverage ≥85% on new `src/mcp_server/` modules
2. `nexus mcp serve` starts cleanly in both stdio and HTTP modes
3. Claude Code can connect via stdio and call all 8 tools successfully (manual smoke)
4. HTTP mode bearer auth rejects unauthenticated requests (integration test)
5. README, ARCHITECTURE, ROADMAP updated for v1.2 (MCP server section)
6. CHANGELOG entry summarizing per-component changes
7. v1.2 git tag, GitHub release notes
8. Example client config (Claude Code `.mcp.json`) in README

---

## 11. 实施阶段

| Phase | Goal | Tasks | Est. | Dependencies |
|---|---|---|---|---|
| **A. Skeleton (B)** | `src/mcp_server/` skeleton + FastMCP app + 1 tool end-to-end | 4 | 2 days | None |
| **B. Tool surface** | Remaining 7 tools + runtime_bridge glue + active_runtimes registry | 6 | 3 days | A (skeleton pattern) |
| **C. Resources + Prompts** | 2 resources + 1 prompt | 3 | 1 day | A |
| **D. Transport + Auth** | HTTP mode + BearerTokenMiddleware + CLI flags | 4 | 2 days | A |
| **E. Tests + docs** | Unit + integration + LLM smoke + README/ARCHITECTURE/ROADMAP rewrite + v1.2 tag | 7 | 3 days | A–D |
| **F. Manual smoke** | Test against real Claude Code + Cline + raw HTTP client | 3 | 1 day | E |

**Total: 27 tasks, ~12 working days (~2.5 weeks at sustainable pace).**

Critical path: **A → B/C/D (parallel) → E → F**.

---

## 12. 风险与缓解

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| FastMCP SDK breaking changes | Medium | High | Pin `mcp[cli]>=1.12,<2.0`; smoke test in CI catches upgrades |
| stdio mode spawn overhead slow | Low | Low | First call ~200ms (Python import); acceptable; cache `_active_runtimes` across calls |
| Bearer token logged by Starlette | Low | High | Explicit log filter; never log `Authorization` header; integration test asserts logs |
| Concurrent tool calls corrupting session state | Medium | High | `_walking_lock` + idempotent abort/pause; integration test for concurrent resume |
| Large session JSON (>1MB) bloats MCP responses | Low | Medium | Resource chunks via pagination in v1.3 if seen in practice; warn at 100KB |
| MCP spec evolution (Apps, streaming) | Medium | Medium | Spec-compliant surface; FastMCP upgrade handles protocol details; surface stays minimal |
| HTTP mode accidentally binds 0.0.0.0 | Medium | High | Default `--host 127.0.0.1`; startup banner warns if 0.0.0.0 + token |
| Token in shell history | High | Medium | README recommends env var; click help mentions `read -s` pattern |
| Client sends malformed tool args | High | Low | FastMCP validates via Pydantic; returns structured error per MCP spec |
| Old MCP client (pre-spec version) connects | Low | Low | FastMCP handles negotiation; log warning if major version mismatch |
| nexus mcp serve runs as root | Medium | High | Startup check: refuse to run as UID 0 unless `--allow-root` flag |
| Memory store query slow for large projects | Medium | Low | Cap `k` at 20; cache episodic index; v1.3 adds pagination |
| Plan generation time exceeds MCP request timeout | Medium | Medium | Default MCP timeout 30s; long plans warn + offer async pattern via `auto_walk=False` |
| Session manager DB locked by another process | Low | Medium | SQLite WAL mode (existing); document "one server per project" |

---

## 13. 未来扩展（v1.3+，超出本文档范围）

- **OAuth 2.1 + PKCE authentication** for HTTP mode (MCP spec recommended)
- **Streaming progress notifications** via MCP `notifications/tools/progress`
- **MCP Apps integration** — render Nexus TUI panels as MCP App UI in compatible clients
- **Prompt templates with variables** — multi-parameter templates (e.g., `nexus_plan_template_with_role_and_model`)
- **Server-sent events (SSE) for walk progress** — clients subscribe to live plan execution
- **Multi-tenant session isolation** — per-user authorization tokens
- **Token rotation + refresh** — for long-running HTTP servers
- **MCP server discovery via mDNS** — auto-discovery on local network
- **Sub-plan WAL replay via MCP** — cursor-level replay through MCP tool
- **Plan diff visualization** — MCP resource showing plan edits before/after

---

## 14. 参考

- v1.1 design spec: `docs/superpowers/specs/2026-06-28-nexus-v11-multi-agent-memory-design.md`
- v1.0 design spec: `docs/superpowers/specs/2026-06-27-nexus-plan-first-redesign-design.md`
- MCP Python SDK: `https://github.com/modelcontextprotocol/python-sdk`
- MCP specification: `https://modelcontextprotocol.io`
- FastMCP tutorial: `https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/tutorial/first-steps.md`
- Existing MCP consumer modules (unchanged):
  - `src/mcp/transport.py` — mcporter CLI wrappers
  - `src/mcp/integration.py` — RalphLoopMCPBridge
  - `src/mcp/presets.py` — GitHub/Slack/Postgres presets
  - `src/mcp/config.py` — MCPConfigManager
  - `src/mcp/bridge.py` — MCPToolBridge
  - `src/mcp/connection.py` — MCPConnectionManager
  - `src/mcp/client.py` — MCP client wrapper
- Reused server-side modules:
  - `src/agent/runtime.py` — AgentRuntime.plan / walk
  - `src/agent/control.py` — ControlChannel.pause / abort
  - `src/agent/events.py` — WalkEvent hierarchy
  - `src/agent/plan.py` — Plan / PlanStep
  - `src/session/manager.py` — SessionManager (create / save / load / restore / list)
  - `src/session/models.py` — SessionData / SessionMetadata / SessionStatus
  - `src/agents/registry.py` — RoleRegistry (v1.1)
  - `src/context/memory.py` — MemoryStore (v1.1)
- New CLI surface: `src/cli/commands/mcp.py` (adds `serve` subcommand)
- New server module: `src/mcp_server/` (NEW)