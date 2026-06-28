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

## v1.1 — Q3 2026 — Sub-plans + MCP ✅

**Delivered:**
- SUBPLAN step kind + RoleRegistry — roles re-use existing role files unchanged
- Three-layer memory: EpisodicIndex (WAL-derived), SemanticIndex (substring + opt-in embeddings), SkillIndex
- Self-evolution feedback loop: Evolver + PromptTemplateRegistry with user-approval gate
- Verification pipelines: named pipelines (security/tdd/test/review) for VERIFY steps
- retry_with_feedback: verifier errors fed back to LLM on step failure
- WAL v2 format: `format_version` header + `metadata` blocks; v1.0 WAL files load without changes
- New CLI commands: `nexus session migrate`, `nexus role`, `nexus memory`, `nexus skill`, `nexus prompt`, `nexus evolve`
- New TUI panels: VerifierPanel, MemoryPanel; new modals: SkillPickerModal, EvolveApprovalModal, PromptHistoryViewerModal
- New keybindings: V, M, s, E, Ctrl-r

**Changes from v1.0:**
- `OnFailure` enum gains `RETRY_WITH_FEEDBACK`
- `PlanStepKind` enum gains `SUBPLAN`
- `PlanStep` gains optional `role`, `subplan_args`, `pipeline`, `pipeline_args` fields
- WAL records gain `format_version` and optional `metadata` blocks

**Migration guide:**
- v1.0 WAL files load in v1.1 without changes
- Optional: `nexus session migrate <plan_id>` produces a v2-normalized copy

**Open questions (deferred):**
- MCP server wiring (deferred to v1.2)
- Model router (deferred to v1.2)
- Real WebSearch tool (deferred to v1.2)

---

## v1.2 — Q4 2026 — Model Router

**Goals:**
- **Real WebSearch tool**: replace v1.0 stub with live search backend (e.g., Anthropic WebSearch API or pluggable provider).
- **Model Router** (v1.2 ship): cost-aware LLM selection — choose model per step based on task complexity, budget, and historical success rates. See `docs/superpowers/specs/2026-06-28-nexus-v12-model-router-design.md`.

**Deferred to v1.3:**
- **MCP Server Mode**: expose Nexus as an MCP server so external clients (e.g., Claude Desktop, other agents) can invoke `nexus run` and reuse the plan-first runtime. v1.3 ships **stdio-only** transport first; HTTP/SSE + OAuth deferred further. See `docs/superpowers/specs/2026-06-28-nexus-v12-mcp-server-design.md`.

**Note:** Design specs are still TODO. Once drafted, place under `docs/superpowers/specs/2026-06-28-nexus-v12-*.md`.

**Cuts from v1.1:** none expected — v1.2 is additive (new tool impl, new router module, new MCP transport).

---

## v1.3 — Q4 2026 — MCP Server Mode (stdio)

**Goals:**
- **MCP Server Mode (stdio-only)**: expose Nexus as an MCP server over stdio so local clients (Claude Code, Cline, Cursor) can invoke plan/session primitives. See `docs/superpowers/specs/2026-06-28-nexus-v12-mcp-server-design.md` (deferred from v1.2).
- **Hard budget cap** (from Model Router spec §13) — kill walk if `budget_usd_per_session` exceeded, rather than v1.2's advisory-only warning.
- **Auto plan-complexity detection** (from Model Router spec §13) — infer hint from prompt length + step count.

**Deferred from v1.3:**
- HTTP / SSE transport + Bearer / OAuth auth (v1.4+).
- Multi-model speculation (v3 TaskForest scope).

---

## v2 — Q1 2027 — Deepening Self-Evolution

> **Note:** v1.1 shipped **foundational** self-evolution — `Evolver` + `PromptTemplateRegistry` with user-approval gate, plus three-layer memory (EpisodicIndex, SemanticIndex, SkillIndex). v2 builds on that foundation rather than reintroducing it.

**Goals:**
- **A/B test framework for prompt variations**: split traffic between prompt variants per role, measure outcomes via WAL-derived success metrics, promote winners automatically.
- **Error-pattern skill library**: automatically mine WAL error patterns → cluster → generate reusable prompt templates (closes the loop from detection to remediation).
- **Skill promotion pipeline**: skill graduates from SkillIndex → PromptTemplateRegistry once it meets a confidence threshold (e.g., used successfully N times).

**Deferred from v1.1 open questions:** now closed via the foundational work above.

**Cuts from v1.1:** none expected.

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
| 2026-06-28 | v1.1 ships sub-plans + MCP + memory + foundational self-evolution | SUBPLAN + RoleRegistry unlocks multi-agent plans; three-layer memory + Evolver enable learning without re-architecting runtime |
| 2026-06-28 | v1.2 plan: WebSearch + Model Router + MCP server mode | Real WebSearch unblocks live research; Model Router controls cost as plans grow; MCP server mode lets external clients reuse Nexus runtime |
| 2026-06-28 | v1.2 scope narrowed: Model Router only; MCP Server Mode deferred to v1.3 (stdio-only) | Keeps v1.2 shippable (~4 weeks); MCP server needs separate SDK dependency + stdio/HTTP transport split |
| 2026-06-28 | v2 reframed: deepening self-evolution (not reintroduction) | v1.1 shipped Evolver + PromptTemplateRegistry; v2 focuses on A/B testing + auto-mined skill library on top of that foundation |

---

## Contributing

See `docs/superpowers/` for plan/spec templates. New features start with a design spec in `docs/superpowers/specs/` followed by a step-by-step plan in `docs/superpowers/plans/`.