# Nexus Roadmap

> **Goal:** Plan-first autonomous coding agent, from MVP to self-evolution.

---

## v1.0 — Plan-First MVP ✅

Released. Full plan in `docs/superpowers/plans/2026-06-27-nexus-plan-first-redesign.md`.

**Delivered:**
- `Plan` + `PlanStep` data model with version semantics
- `WalkEvent` hierarchy (11 events)
- `ControlChannel` (asyncio queues + pause event)
- `PlanWalker` (TOOL/VERIFY/CRITIQUE/ASK_USER step kinds, on_failure strategies)
- `Planner` (LLM → structured Plan with JSON retry + markdown stripping)
- `AgentRuntime` (orchestrates Planner + Walker + WAL)
- WAL step-level JSONL checkpoint + recover
- Textual TUI: NexusApp + PlanPanel + ExecutionPanel + ToolOutputPanel + StepEditModal + RecoverModal + NewTaskModal
- Subscriber-based event dispatcher (fixes multi-panel race)
- 8 built-in tools (Read/Write/Edit/Bash/Glob/Grep/Git/WebSearch stub)
- CLI: `nexus run`, `nexus tui`, `nexus session list/resume`
- 107 tests passing (Agent: 23, TUI: 11, Tools: 17, Integration: 11, WAL: 3, CLI: 4, LLM smoke: 3-skipped)

**Cuts (deferred from RalphLoop era):**
- Subagents
- TDD enforcer
- Self-evolution engine
- MCP server wiring
- Sub-plans
- SQLite checkpoints (replaced by WAL JSONL)

---

## v1.1 — Q3 2026 — Sub-plans + MCP

**Goals:**
- **Sub-plans:** A TOOL step can return a sub-Plan that the walker executes inline (enables hierarchical task decomposition).
- **MCP server wiring:** Expose Nexus itself as an MCP server so other tools (e.g., Claude Code) can invoke `nexus.plan` and `nexus.walk`.
- **Model router:** Multi-provider support (Anthropic / OpenAI / Ollama / SCNET) routed by task complexity.
- **Real WebSearch tool:** Replace v1 stub with Anthropic SDK web search tool wiring.
- **Persistent plan storage:** Persist full `Plan.to_dict()` in WAL so `nexus session resume` can fully reconstruct, not just show cursor.

**Open questions:**
- Sub-plan abort semantics (does sub-plan abort bubble up to parent?)
- MCP server: stdio or HTTP transport?

---

## v2 — Q4 2026 — Self-Evolution Return

**Goals:**
- Reintroduce `SelfEvolutionEngine` to learn from WAL error patterns.
- Skill library: error → pattern → reusable prompt template.
- A/B test framework for prompt variations.

**Cuts from v1:** none expected.

---

## v3 — 2027 — Multi-Agent Speculation

**Goals:**
- Replace sequential `PlanWalker` with `TaskGraph` executor: independent steps run in parallel via asyncio.gather().
- Speculative execution: when Plan A is walking step N, generate Plan B for step N+1 in parallel.
- Dynamic replan: walker can request planner to regenerate Plan mid-execution if events indicate plan is failing.

**Risks:**
- Race conditions in shared state (Plan, tools, WAL) — needs careful locking.
- LLM cost explosion without rate limiting.

---

## Decision Log

| Date | Decision | Why |
|------|----------|-----|
| 2026-06-27 | Plan-first MVP (v1.0) | RalphLoop state machine was opaque; user wanted explicit, reviewable plans |
| 2026-06-27 | Drop subagents/TDD/SelfEvo from v1 | Focus on core plan-walk-recover loop; add complexity later |
| 2026-06-27 | Textual TUI replaces Rich+readchar | Modern, async-native, easier to maintain than legacy Rich loop |
| 2026-06-27 | WAL JSONL replaces SQLite | Simpler, append-only, no schema migrations |

---

## Contributing

See `docs/superpowers/` for plan/spec templates. New features start with a design spec in `docs/superpowers/specs/` followed by a step-by-step plan in `docs/superpowers/plans/`.