# Nexus Architecture — Plan-First v1

> **Version:** 1.0
> **Last updated:** 2026-06-27
> **Goal:** Plan-first autonomous coding agent — every task becomes an explicit, editable `Plan` before any tool runs.

---

## 1. Top-level Flow

```
CLI (src/cli/main.py)
  ├─ nexus run  ──► run.py        ──► AgentRuntime.plan() → walk()
  ├─ nexus tui   ──► tui.py       ──► NexusApp.run() ↔ AgentRuntime
  └─ nexus session ──► session.py ──► WALManager (list/resume)

NexusApp (src/tui/app.py)
  ├─ _dispatch_events (single drainer, 50ms) ──► subscriber callbacks
  │     ├─ PlanPanel subscribers ──► Tree node markers
  │     ├─ ExecutionPanel subscribers ──► RichLog lines
  │     └─ ToolOutputPanel subscribers ──► Static update
  └─ _drain_commands (50ms) ──► runtime.{walk, edit_step, insert_step, ...}

AgentRuntime (src/agent/runtime.py)
  ├─ plan() ──► Planner.plan() ──► LLM → JSON → Plan
  ├─ walk() ──► PlanWalker.walk() ──► step-by-step event emission
  ├─ edit_step / insert_step / remove_step / reorder_steps ──► bump Plan.version
  └─ pause / resume / abort ──► ControlChannel.{pause, resume, abort}

PlanWalker (src/agent/walker.py)
  ├─ iterates Plan.steps[]
  ├─ before each step: channel.wait_if_paused(); channel.is_aborted check
  ├─ _execute_step() dispatches by kind:
  │     ├─ TOOL      ──► ToolRegistry.execute() ──► ToolCallStarted/Completed events
  │     ├─ VERIFY    ──► verification.run() ──► StepFailed if !passed
  │     ├─ CRITIQUE  ──► llm.complete() ──► StepFailed if !passes
  │     └─ ASK_USER  ──► blocks until channel.recv_command(ANSWER_QUESTION)
  ├─ _handle_step_failure() dispatches by on_failure:
  │     ├─ ABORT  ──► raise PlanAborted
  │     ├─ SKIP   ──► StepResult(status="skipped")
  │     ├─ RETRY  ──► up to MAX_RETRIES_PER_STEP
  │     └─ ASK    ──► block until user answers skip/retry/abort
  └─ after successful step: WALManager.checkpoint(plan, cursor=step.id, result)

WALManager (src/context/wal.py)
  ├─ checkpoint() ──► append {tx, plan_id, version, cursor, result} to JSONL
  ├─ recover()   ──► read JSONL, return (Plan, last_cursor)
  └─ get_completed_step_ids(plan_id) ──► set of cursors

ControlChannel (src/agent/control.py)
  ├─ _events      (asyncio.Queue[WalkEvent])
  ├─ _commands    (asyncio.Queue[Command])
  ├─ _pause_event (asyncio.Event)
  └─ _aborted, _abort_reason (sync flags)
```

---

## 2. Component Table

| Component | File | Lines | Responsibility |
|-----------|------|-------|----------------|
| `Plan`, `PlanStep`, `OnFailure`, `PlanStepKind` | `src/agent/plan.py` | 90 | First-class plan artifact + version semantics |
| `WalkEvent` hierarchy (11 types) | `src/agent/events.py` | 115 | All observable events |
| `ControlChannel`, `Command`, `CommandKind`, `StepResult` | `src/agent/control.py` | 100 | Bidirectional async channel + pause/abort |
| `PlanWalker` | `src/agent/walker.py` | 240 | Sequential step execution + failure handling |
| `Planner` | `src/agent/planner.py` | 95 | LLM → JSON → Plan with retry |
| `AgentRuntime` | `src/agent/runtime.py` | 75 | Orchestrates Planner + Walker + WAL + channel |
| `WALManager` | `src/context/wal.py` | 80 | JSONL checkpoint + recover |
| `NexusApp` | `src/tui/app.py` | 180 | Textual app shell + dispatcher |
| `PlanPanel` | `src/tui/plan_panel.py` | 180 | Tree view of plan steps + bindings |
| `ExecutionPanel` | `src/tui/execution_panel.py` | 90 | RichLog of walker events |
| `ToolOutputPanel` | `src/tui/tool_output_panel.py` | 60 | Last tool I/O |
| `StepEditModal` | `src/tui/step_edit_modal.py` | 200 | Edit one step's 6 fields |
| `RecoverModal` | `src/tui/recover_modal.py` | 70 | Resume/Discard prompt at startup |
| `NewTaskModal` | `src/tui/new_task_modal.py` | 50 | Capture user task input |
| `Tool` Protocol + `ToolRegistry` | `src/tools/base.py`, `src/tools/registry.py` | 90 | Tool discovery + dispatch |
| 8 tools | `src/tools/{read,write,edit,bash,glob,grep,git,web_search}.py` | ~80 each | Built-in tools |

---

## 3. Design Decisions

### 3.1 Single asyncio event loop, no threads, no locks

**Constraint:** TUI ↔ Runtime communication uses one asyncio event loop. No `threading.Thread`, no `threading.Lock`.

**Why:** The legacy RalphLoop design used `threading.Thread` to bridge Rich Live display (sync) with the async executor. This required locks everywhere and introduced subtle race conditions. Textual is async-native, so the TUI naturally runs on the same loop as the runtime.

**Trade-off:** Cannot run blocking I/O on the UI thread. All tools that need subprocess I/O use `asyncio.create_subprocess_shell` / `create_subprocess_exec` with `await proc.communicate()`.

### 3.2 Pause only at step boundaries — never mid-tool

**Constraint:** The walker may only pause between steps, not during a tool call.

**Why:** Tools that have side effects (Write, Edit, Bash) must complete or fail atomically. Pausing mid-tool would leave the system in an undefined state (e.g., half-written file).

**Trade-off:** A long-running Bash command cannot be interrupted by the user. Mitigation: each step has a `timeout_s` (default 120) — if exceeded, the walker kills the subprocess.

### 3.3 `on_failure` default = `"ask"` (not `"retry"`)

**Constraint:** A new PlanStep with no explicit `on_failure` defaults to `"ask"` — not `"retry"`.

**Why:** Silent retries can mask real bugs (e.g., a syntax error in code that retrying won't fix). Asking the user forces an explicit decision: skip, retry, abort.

**Trade-off:** More user interaction required for flaky tools. Mitigation: tool authors can set `on_failure="retry"` for known-flaky tools.

### 3.4 Subscriber-based event dispatcher (not per-panel queue draining)

**Constraint:** Only one component (NexusApp) drains `ControlChannel._events`. Panels subscribe via `app.subscribe_event(EventType, callback)`.

**Why:** The first Textual implementation had each panel running its own `set_interval(0.1, _drain_events)`. Whichever interval fired first claimed the event, others dropped it — non-deterministic. The dispatcher pattern guarantees every event reaches every interested panel.

**Trade-off:** All event handling runs on the Textual event loop tick. Heavy event bursts could slow the UI. Mitigation: events are simple dataclasses; rendering is deferred to widget internals.

### 3.5 WAL step-level checkpoint (not full plan snapshot)

**Constraint:** WALManager writes one JSONL line per completed step: `{plan_id, version, cursor=step_id, result}`. The full Plan is *not* persisted in v1.

**Why:** Step-level checkpoint is enough to detect which steps are done on recovery. Persisting the full Plan on every edit would be expensive and would conflict with the "Plan is mutable, versioned" model.

**Trade-off:** `nexus session resume <id>` can show last cursor but cannot fully reconstruct the plan — for that, the user runs `nexus tui` which prompts via RecoverModal (which has the plan_id from WAL). v1.1 will persist `Plan.to_dict()` in WAL to enable full reconstruction.

### 3.6 `Plan.version` bumps on any mutation

**Constraint:** `edit_step`, `insert_step`, `remove_step`, `reorder_steps` all bump `Plan.version`. The WAL checkpoint records the version.

**Why:** Distinguishes "same plan, different step edits" from "different plan". When recovering, the walker checks `get_completed_step_ids(plan_id)` — if a step ID is gone (due to reorder or remove), it's treated as un-completed and re-executed.

**Trade-off:** A reordered or removed step after checkpoint is silently re-run. Mitigation: TUI confirmation before mutations.

---

## 4. Async Concurrency Model

All components share one asyncio event loop:

```
Textual App (main loop)
  ├─ asyncio.create_task(runtime.walk())      # spawned on 'a' (approve)
  │     └─ PlanWalker.walk()
  │           ├─ await channel.wait_if_paused()   # respects pause_event
  │           ├─ await tools.execute(...)           # subprocess via asyncio
  │           └─ await wal.checkpoint(...)
  ├─ set_interval(0.05, _dispatch_events)     # single drainer
  └─ set_interval(0.05, _drain_commands)      # single drainer
```

No threads → no GIL contention → no lock contention.

---

## 5. Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Crash mid-walk | Process restart + WAL replay | `NexusApp.on_mount` checks `wal.recover()`, shows RecoverModal |
| Step timeout | `asyncio.wait_for(proc.communicate(), timeout=timeout_s)` | `_handle_step_failure` → user strategy |
| Tool raises exception | try/except in `_execute_tool_step` | Same as step failure |
| LLM returns invalid JSON | JSON parse failure in Planner | Retry up to `max_retries` (default 3) |
| Tool returns malicious code | `verification.security_scan` | `StepFailed` → on_failure strategy |
| User pauses mid-walk | `_pause_event.clear()` | Walker blocks at next `wait_if_paused()` |
| User aborts mid-walk | `_aborted = True` | Walker raises `PlanAborted` after current step |

---

## 6. Performance Budget

Per step (typical):
- Event emission: <1ms (3-5 events per step)
- Tool execution: 10-1000ms (depends on tool)
- WAL checkpoint: <5ms (append + fsync)
- Subscriber dispatch: <1ms per panel

Total overhead per step: <10ms excluding tool time.

---

## 7. Test Strategy

- **Unit:** Each component in isolation (Plan, ControlChannel, individual tools)
- **Integration:** AgentRuntime + Planner + Walker + WAL end-to-end with FakeLLM
- **TUI:** Textual `app.run_test()` async harness — verify widget state after key presses and event emissions
- **Smoke:** Real LLM via `ANTHROPIC_API_KEY` — skipped without key
- **Recovery:** Simulate crash by writing WAL then starting fresh NexusApp → assert auto-skip

---

## 8. Future Architecture (v1.1+)

- **Sub-plans:** A TOOL step returns a sub-Plan; walker executes inline with shared WAL.
- **MCP server:** Expose `nexus.plan` and `nexus.walk` as MCP tools.
- **Multi-agent speculation:** Replace sequential walker with `TaskGraph` executor; independent steps run via `asyncio.gather`.
- **Self-evolution engine:** Re-read WAL error patterns to suggest prompt improvements.

See `ROADMAP.md` for timeline.