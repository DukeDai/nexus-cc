# Nexus — Plan-First Autonomous Coding Agent

Nexus is a Claude Code alternative with a **plan-first architecture**: every task becomes an explicit, editable `Plan` of typed `PlanStep`s before any code runs. The PlanWalker executes steps sequentially, pausing at boundaries, persisting checkpoints, and recovering from crashes.

> **Different from Claude Code:** Nexus treats the Plan as a first-class artifact you can review, edit, and approve *before* execution. Every step emits an event; every step is checkpointed; every tool call is observable.

---

## What's New in v1.1

- **Sub-agent roles:** `SUBPLAN` step kind + `RoleRegistry` wiring — roles re-use existing role files unchanged
- **Three-layer memory:** `EpisodicIndex` (WAL-derived), `SemanticIndex` (substring + opt-in embeddings), `SkillIndex` (wraps existing loader)
- **Self-evolution loop:** `Evolver` + `PromptTemplateRegistry` with user-approval gate — learns from WAL error patterns
- **Verification pipelines:** `VERIFY` steps reference named pipelines (security/tdd/test/review)
- **Retry-with-feedback:** `on_failure="retry_with_feedback"` feeds verifier errors back to the LLM
- **WAL v2:** `format_version` header + `metadata` blocks; v1.0 WAL files still load

---

## Why plan-first?

| Aspect | Claude Code | Nexus v1 |
|--------|-------------|----------|
| Plan visibility | Implicit in tool sequence | First-class `Plan` object — visible, editable, versioned |
| User review | Tool-by-tool approval | Whole-plan review before any tool runs |
| Pause points | Mid-tool only | **Only at step boundaries** — atomic, predictable |
| Crash recovery | Resume from session | WAL replay + auto-skip completed steps |
| Edit step intent | Restart conversation | Edit single step in modal → bumps `Plan.version` |
| Tool output | Log scrollback | Dedicated `ToolOutputPanel` with last I/O |

---

## Install

```bash
git clone https://github.com/DukeDai/nexus-cc.git
cd nexus-cc
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test,embeddings]"   # embeddings extra enables semantic memory
```

Requires Python ≥ 3.12 and `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) for real LLM calls.

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `nexus run --task "<prompt>"` | One-shot plan-build-and-walk |
| `nexus tui` | Interactive TUI |
| `nexus session list` | List plan IDs in WAL |
| `nexus session resume <id>` | Show last cursor; full resume in TUI |
| `nexus session migrate <id>` | Migrate WAL to v2 format |
| `nexus role <name> [--file <path>]` | Show / register a role |
| `nexus memory [query]` | Query episodic + semantic memory |
| `nexus memory --index <path>` | Rebuild semantic index |
| `nexus skill [query]` | Search skill index |
| `nexus prompt [list\|show <name>]` | List / show prompt templates |
| `nexus evolve [--dry-run]` | Run self-evolution on WAL patterns |

---

## Quick Start

### CLI — one-shot task

```bash
nexus run --task "在 src/foo.py 加一行注释 '# updated by nexus'"
nexus run --task "把所有 *.md 文件转为 snake_case 文件名" --workdir ./myproject
```

This builds a `Plan`, walks it, and writes per-step JSONL checkpoints to `.nexus/wal.jsonl`.

### TUI — interactive review and execution

```bash
nexus tui
```

Press `n` to enter a task → plan appears in left pane → review steps → press `a` to approve → watch execution live in the right panes.

```
┌────────────────────────────────────────────────────────────────────────┐
│ Plan Pane (40%)            │ Execution Pane (50%)                      │
│  ▼ plan_abc12345           │  ▶ Step 1/3: Read config                 │
│   ▶ Read config.yml        │  → Read({'path': 'config.yml'})          │
│   ✓ Update value           │  ✓ Read done                              │
│   ✓ Write back             │  ✓ Step 1 complete                        │
│                            ├───────────────────────────────────────────┤
│                            │ Tool Output Pane (50%)                    │
│                            │ → Read                                    │
│                            │ args: {'path': 'config.yml'}             │
└────────────────────────────────────────────────────────────────────────┘
```

### Session — recover or inspect

```bash
nexus session list                # list plan IDs in WAL
nexus session resume plan_abc12345 # show last cursor; full resume via TUI
```

---

## TUI Key Bindings

| Key | Action | Notes |
|-----|--------|-------|
| `n` | New task | Opens input modal |
| `a` | Approve plan | Begin walking |
| `r` | Reject plan | Discard |
| `e` | Edit step | Opens StepEditModal with 6 fields |
| `d` | Delete step | Fires REMOVE_STEP command |
| `i` | Insert step | Fires INSERT_STEP command |
| `J` / `K` | Move step down / up | Reorder |
| `p` / `P` | Pause / Resume | At next step boundary |
| `x` | Abort | Immediate |
| `j` / `k` | Cursor down / up | In tree |
| `V` | Open VerifierPanel | View / re-run verification pipeline |
| `M` | Open MemoryPanel | Browse episodic + semantic memory |
| `s` | Open SkillPickerModal | Search and insert skill |
| `E` | Open EvolveApprovalModal | Review / approve evolution suggestions |
| `Ctrl-r` | Re-run verifier | Re-execute current step's verification |
| `?` | Help | |
| `ctrl+c` | Quit | |

---

## Architecture

```
User ──► CLI / TUI ──► ControlChannel (asyncio queues + pause event)
                              │
                              ▼
                     ┌────────────────────┐
                     │   AgentRuntime     │
                     │                    │
                     │  plan() ─► Planner │
                     │             │      │
                     │             ▼      │
                     │   Plan (List[Step])│
                     │             │      │
                     │             ▼      │
                     │  walk() ─► Walker  │
                     │             │      │
                     │             ▼      │
                     │   WALManager.check │
                     └─────────┬──────────┘
                               │
                               ▼
                     ToolRegistry (8 tools)
```

**Key components:**

- **`Plan`** (`src/agent/plan.py`) — dataclass with `steps: list[PlanStep]`, `version: int`. Mutating bumps version.
- **`PlanStep`** — `kind: TOOL | VERIFY | CRITIQUE | ASK_USER`, `tool`, `args`, `success_criteria`, `on_failure`.
- **`WalkEvent`** (`src/agent/events.py`) — `PlanStarted`, `StepStarted`, `ToolCallStarted`, `ToolCallCompleted`, `StepCompleted`, `StepFailed`, `AskUser`, `Paused`, `Resumed`, `Aborted`, `PlanCompleted`.
- **`ControlChannel`** (`src/agent/control.py`) — two asyncio queues (`_events`, `_commands`) + `_pause_event`. No callbacks, no locks.
- **`AgentRuntime`** (`src/agent/runtime.py`) — orchestrates `Planner → PlanWalker → WALManager`, exposes `edit_step / insert_step / remove_step / reorder_steps`.
- **`NexusApp`** (`src/tui/app.py`) — Textual app; single `_dispatch_events` loop fans events to subscribed panels (avoids multi-panel race).

---

## Tools

The bundled `ToolRegistry.with_defaults(workdir=".")` registers 8 built-in tools:

| Tool | Purpose |
|------|---------|
| `Read` | Read file contents, optional line range |
| `Write` | Write content to file, creates parent dirs |
| `Edit` | Atomic string replacement, single or replace_all |
| `Bash` | Run shell commands with dangerous-pattern detection |
| `Glob` | Find files by glob, recursive `**` |
| `Grep` | Regex search with file:line:content output |
| `Git` | Safe wrapper over git CLI (whitelist of subcommands) |
| `WebSearch` | Stub for v1; real impl in v2 |

---

## Running Tests

```bash
# All tests (requires ANTHROPIC_API_KEY only for LLM smoke tests — they skip otherwise)
PYTHONPATH=./src .venv/bin/python -m pytest tests/ -v

# Just plan-first agent tests
PYTHONPATH=./src .venv/bin/python -m pytest tests/agent/ tests/integration/ -v
```

Test counts (as of v1.0.0):
- Agent core: 23 tests
- TUI: 11 tests
- Tools: 17 tests
- Integration (plan-review + crash-recovery + CLI): 11 tests
- WAL: 3 tests
- LLM smoke: 3 (skipped without API key)

---

## Roadmap

- ✅ **v1.0 (this release)** — Plan/AgentRuntime/ControlChannel/Textual TUI/WAL/8 tools/CLI.
- ⏳ **v1.1** — Sub-plans, MCP server wiring, model router for multi-provider.
- 🔮 **v2** — Self-evolution engine return (skill learning from WAL error patterns).
- 🔮 **v3** — Multi-agent speculative execution (replaces current sequential walker).

See `ROADMAP.md` for the full timeline.

---

## License

MIT — see `LICENSE`.