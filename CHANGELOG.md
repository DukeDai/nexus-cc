# Changelog

## v1.2.0 (2026-06-30)

**29 commits since v1.1.0** (`960b384`..`2f4d9bf`). Compare: https://github.com/[org]/nexus-cc/compare/v1.1.0...v1.2.0

### Added
- **Model Router** (feature-flagged via `NEXUS_USE_MODEL_ROUTER=1`, default off so v1.1 behavior is preserved exactly)
  - `ModelHint` enum (`planner` / `critique` / `verifier_security` / `verifier_review` / `evolver` / `default`) at `src/llm/router.py` — per-call-site model selection hint resolved by `ModelRouter` against `.nexus/policy.yaml`.
  - `ModelPolicy` dataclass at `src/llm/policy.py` — 4-tier resolution: `--model` CLI flag → `NEXUS_MODEL_<HINT>` env vars → `.nexus/policy.yaml` → built-in `DEFAULT_POLICY`.
  - `CostTracker` at `src/llm/cost.py` — in-memory ring buffer (last 1000 records) + WAL `kind="llm_cost"` JSONL append + aggregation by `model` / `hint` / `role` / `session`.
  - Router injection wraps `LLMClient.complete()` and emits a `CostRecord` per call; existing call sites get hint-aware routing without behavior change when hints are unset (default branch returns v1.1 Sonnet 4.6).
  - **Hint wiring** into every call site: `Planner.plan()`, `Walker._execute_critique_step()`, `ReviewGate` delegates (spec/logic/security), and `VerificationPipeline` (replaces the legacy `noop_delegate`).
  - `RoleRegistry.spawn` now consumes `RoleDefinition.model_tier` (FAST → Haiku 4.5, SONNET → Sonnet 4.6, OPUS → Opus 4.8) and forwards the resolved model name as a hint.
  - **MiniMax-M3 / MiniMax-M2.7 first-class models** — re-exposed via the Anthropic-compatible endpoint at `https://api.minimaxi.com/anthropic`; pricing tier-equivalent to Sonnet/Haiku 4.x as a starting estimate.
  - 41 new router tests (model_policy 12, cost_tracker 14, model_router 13, integration 3); coverage on new modules: `model_policy` 100%, `cost_tracker` 100%, `model_router` 91.5%.
- **CLI** (`nexus cost`, `nexus model`)
  - `nexus cost today|by-model|by-role|session <id>|export --csv|summary` — read WAL `CostRecord`s with custom loader (no external deps).
  - `nexus model list|show <name>|resolve` — exposes `DEFAULT_POLICY` + `.nexus/policy.yaml` + env overrides + cli_override + alias model names.
  - `--model` flag in `nexus run` wired to `ModelPolicy.load(cli_model=...)`.
  - Auto-create starter `.nexus/policy.yaml` on first `nexus run` (fails-soft, never overwrites existing files).
  - 25 new CLI tests (12 cost, 10 model, 3 ensure_policy).
- **TUI v1.2 surfaces**
  - `CostPanel` — live session cost rollup, subscribes to `StepCompleted` so totals refresh after each step.
  - `ModelMappingModal` (Shift+M) — one editable `Input` per `ModelHint`; Save writes `.nexus/policy.yaml` defaults and pushes the updated `ModelPolicy` back into the runtime via `set_policy`.
  - Per-step model badge — `_resolve_step_model(step, policy)` maps every `PlanStep` (SUBPLAN+role / VERIFY+pipeline / CRITIQUE / TOOL / ASK_USER) to the model name the runtime will actually use, then `_short_model_tag` renders it (`[Sonnet]`, `[Haiku]`, `[M3]`, `[M2.7]`, `[Opus]`, `[mini]`).
  - 11 new tests (`CostPanel` 6, `ModelMappingModal` 5) + 23 parametrized tests in `test_resolve_step_model.py` covering the full 5 step-kind × pipeline × role × policy matrix.
- **Real WebSearch** (`src/tools/web_search.py`)
  - Server-side `web_search_20250305` tool type via the Anthropic SDK (GA in `anthropic>=0.97`).
  - Default model `claude-haiku-4-5-20251001` (cheap routing); `max_results` kwarg maps to SDK `max_uses`.
  - 4 new mocked tests (no API key needed): results, answer-text fallback, error propagation, metadata.
- **Planner arg-schema self-correction loop** (closes 3 documented flaky smoke tests: `test_smoke_add_comment`, `test_smoke_rename_files`, `test_smoke_fix_pytest`)
  - Validates each TOOL step's `args` against `ToolRegistry.get(tool).args_schema` and re-prompts the LLM with the validation error.
  - `max_arg_schema_retries=0` disables validation entirely (legacy escape hatch for v1.1 behavior).
  - Non-`ToolRegistry` tools containers bypass the loop automatically (preserves existing fake/mock-based tests).
  - 315-line parametrized test file pins the contract: valid-first-try / invalid-then-valid / invalid-forever / max-retries=0 / legacy signature / VERIFY+CRITIQUE-only plans.
- **Backwards-compat + integration tests** (Task #22)
  - `tests/integration/test_wal_backcompat.py` — replays 3 v1.1 WAL fixtures (simple, failed, multi_step) via `WALManager.iter_records`; verifies v1 step-completion cursor recovery and mixed v1+v2 readability.
  - `tests/integration/test_router_cost_wal.py` — plan with one step per `ModelHint` kind (PLANNER / CRITIQUE / VERIFIER_REVIEW / VERIFIER_SECURITY); verifies `ModelRouter.route()` resolves to the correct model, WAL `append_cost()` is called once per routed call, `CostTracker.aggregate_by('hint')` reflects all routed hints, and WAL append failure is non-fatal.
- **CI** (`.github/workflows/ci.yml`)
  - GitHub Actions workflow triggered on push/PR to `main`.
  - Python 3.12 matrix (matches `pyproject.toml` `requires-python = ">=3.12"`).
  - Concurrency group cancels in-progress runs on the same ref.
  - `pytest --cov=src` with the coverage gate driven by `pyproject.toml` `fail_under`.
  - `ANTHROPIC_API_KEY` passed via secrets (optional; smoke tests skip cleanly when unset).
  - Coverage artifacts uploaded with 14-day retention; optional Codecov upload guarded by `CODECOV_TOKEN`.
  - `pytest-cov` now lives in `[project.optional-dependencies].test` so CI installs it implicitly via `pip install .[test]`.

### Changed
- **Canonicalized imports to `src.ralphloop.*`** — dropped the legacy `sys.path` hack (ac651b5). All internal modules now use the proper `src.ralphloop.*` namespace; external callers must add `src/` to `PYTHONPATH` or install as a package instead of relying on the removed sys.path bootstrap.
- **WebSearchTool now raises `NotImplementedError`** instead of fake success (d95a235 — pre-1.2 cleanup) — users must implement or replace the tool before calling; the real SDK-backed implementation lands in `feat(tools): wire real WebSearch via Anthropic SDK web_search_20250305` (428eafc, see Added above).
- `VERIFIER_SECURITY` default mapping = `claude-haiku-4-5` (the one deliberate cost-downgrade per v1.2 spec §1.2). All other hints default to `claude-sonnet-4-6` (matches v1.1 behavior — backward-compatible).
- `LLMClient` accepts and forwards an optional `model_hint` kwarg; the legacy `LLMClient` absorbs it via `**kwargs` so behavior is unchanged when `NEXUS_USE_MODEL_ROUTER` is unset.
- `Planner.plan()` and `AgentRuntime.plan_subplan()` both gain an optional `model_name` kwarg (default `None`) that preserves pre-v1.2 behavior when unset.
- `VerificationAdapter.__init__` accepts an optional `llm` parameter (default `None`); `register_defaults()` wires a combined delegate that dispatches on `ctx['review_type']`.
- `_env_key()` fallback chain extended: `ANTHROPIC_API_KEY` → `ANTHROPIC_AUTH_TOKEN` → `MINIMAX_API_KEY` → `""` (so users can pick whichever env var they prefer for the MiniMax Anthropic-compatible endpoint).

### Fixed
- **`Planner` arg-schema validation** no longer fires for custom (non-`ToolRegistry`) tool containers used by tests (`FakeToolRegistry`, `MagicMock` with only `.execute()`, `None`); isinstance gate added in 38fe5c6 / e248d91 so existing tests keep working.
- **`subplan_e2e` lambda mock** updated to accept the new `model_name` kwarg introduced by `RoleRegistry.spawn` (34cec45).
- **`main.py` entry point** now imports `sys` so `python -m src.cli.main <subcmd> --help` works (c8e59d6).
- **Coverage source path** corrected from non-existent `nexus` package to the actual importable `src/` root; `relative_files = true` keeps display paths stable across CWDs.
- **WebSearchTool** no longer returns a fake success — raises `NotImplementedError` until the real SDK-backed implementation lands (d95a235 — pre-1.2 cleanup).
- **Planner SYSTEM_PROMPT** now lists the 8 real tool names (Read, Write, Edit, Bash, Glob, Grep, Git, WebSearch); smoke test assertions decoupled from step-kind distribution and assert outcomes (file modified, files renamed, broken test fixed) instead of plan shape.
- **Imports** — canonicalized to `src.ralphloop.*`; dropped the legacy `sys.path` hack.

### Breaking
- **OpenAI / Ollama providers dropped from `DEFAULT_MODELS`.** v1.2 ships Anthropic + MiniMax by default; the router registry drops OpenAI/Ollama entries from `ModelConfig`. Users on those providers must add explicit entries to `.nexus/policy.yaml`.

  Migration — restore OpenAI `gpt-4o-mini` for all hints:

  ```yaml
  # .nexus/policy.yaml
  defaults:
    default: gpt-4o-mini
  ```

  Replace `gpt-4o-mini` with your preferred model; per-hint overrides also supported (e.g. `verifier_security: gpt-4o-mini`, `planner: gpt-4o`). See `docs/superpowers/specs/2026-06-28-nexus-v12-model-router-design.md` §5.

  **Note (revision):** MiniMax (via the Anthropic-compatible API at `https://api.minimaxi.com/anthropic`) is re-exposed as a first-class family in `ModelRouter.DEFAULT_MODELS` (`MiniMax-M3`, `MiniMax-M2.7`). Pricing is a rough tier-equivalent to Anthropic Sonnet/Haiku — override in `cost_tracker.PRICING_PER_1K_TOKENS` if your contract differs. See `.nexus/policy.yaml.example` for a starter.

- **WAL v2 consumers must handle v1 legacy records.** `WALManager` (introduced in v1.1) still loads v1.0 WAL files via `iter_records()` for backward compatibility, but the `format_version=1` records are read-only — write paths only emit v2 records (with `metadata` blocks). Consumers parsing v1 WAL JSON directly (bypassing `WALManager`) will see new keys (`format_version`, optional `metadata`) and must tolerate them. The integration test `tests/integration/test_wal_backcompat.py` pins the v1+v2 mixed-readability contract. Run `nexus session migrate <plan_id>` (added in v1.1) to produce a v2-normalized copy of any legacy WAL.

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