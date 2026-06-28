# Changelog

## Unreleased — v1.2 prep (Model Router only)

### Added (Model Router — v1.2)
- `ModelHint` enum (`planner` / `critique` / `verifier_security` / `verifier_review` / `evolver` / `default`) at `src/llm/router.py` — per-call-site model selection hint resolved by `ModelRouter` against `.nexus/policy.yaml`.
- `ModelPolicy` dataclass at `src/llm/policy.py` — 4-tier resolution: `--model` CLI flag → `NEXUS_MODEL_<HINT>` env vars → `.nexus/policy.yaml` → built-in `DEFAULT_POLICY`.
- `CostTracker` at `src/llm/cost.py` — in-memory ring buffer (last 1000 records) + WAL `kind="llm_cost"` JSONL append + aggregation by `model` / `hint` / `role` / `session`.
- Router injection is **feature-flagged**: `ModelRouter.route(hint=...)` wraps `LLMClient.complete()` and emits a `CostRecord` per call; existing call sites get hint-aware routing without behavior change when hints are unset (default branch returns v1.1 Sonnet 4.6).
- CLI: `nexus cost today|by-model|by-role|session <id>|export --csv`; `nexus model list|show <name>`.
- TUI: `CostPanel` (live USD accumulation), `ModelMappingModal` (Shift+M), per-step model badge.

### Changed
- `VERIFIER_SECURITY` default mapping = `claude-haiku-4-5` (the one deliberate cost-downgrade per spec §1.2). All other hints default to `claude-sonnet-4-6` (matches v1.1 behavior — backward-compatible).

### Breaking
- **OpenAI / Ollama / MiniMax_CN providers dropped from `DEFAULT_MODELS`.** v1.2 ships Anthropic-only by default; the router registry drops OpenAI/Ollama/MiniMax_CN entries from `ModelConfig`. Users on those providers must add explicit entries to `.nexus/policy.yaml`.

  Migration — restore OpenAI `gpt-4o-mini` for all hints:

  ```yaml
  # .nexus/policy.yaml
  defaults:
    default: gpt-4o-mini
  ```

  Replace `gpt-4o-mini` with your preferred model; per-hint overrides also supported (e.g. `verifier_security: gpt-4o-mini`, `planner: gpt-4o`). See `docs/superpowers/specs/2026-06-28-nexus-v12-model-router-design.md` §5.

### Deferred to v1.3
- **MCP Server Mode** — stdio-only transport first; HTTP / SSE / OAuth deferred further. See `docs/superpowers/specs/2026-06-28-nexus-v12-mcp-server-design.md`.

---

## v1.1.0 (2026-06-28)

### Added
- Sub-agent role wiring via SUBPLAN step kind + RoleRegistry (reuses existing role files unchanged)
- Three-layer memory: EpisodicIndex (WAL-derived), SemanticIndex (substring + opt-in embeddings), SkillIndex (wraps existing loader)
- Self-evolution feedback loop: Evolver + PromptTemplateRegistry with user approval gate
- Verification pipeline integration: VERIFY steps can reference named pipelines (security/tdd/test/review)
- Retry-with-feedback: `on_failure="retry_with_feedback"` feeds verifier errors back to LLM
- WAL v2 format with `format_version` header + `metadata` blocks; v1.0 WAL files still load
- New CLI commands: `nexus session migrate`, `nexus role`, `nexus memory`, `nexus skill`, `nexus prompt`, `nexus evolve`
- New TUI panels: VerifierPanel, MemoryPanel, ExecutionPanel
- New TUI modals: SkillPickerModal, EvolveApprovalModal, PromptHistoryViewerModal
- New TUI keybindings: V (verifier), M (memory), s (skill), E (evolve), Ctrl-r (re-run verifier)

### Changed
- `OnFailure` enum gains `RETRY_WITH_FEEDBACK`
- `PlanStepKind` enum gains `SUBPLAN`
- `PlanStep` gains optional `role`, `subplan_args`, `pipeline`, `pipeline_args` fields
- WAL records gain `format_version` and optional `metadata` blocks

### Migration
- v1.0 WAL files load in v1.1 without changes
- Optional: `nexus session migrate <plan_id>` produces a v2-normalized copy
