# Nexus v1.1 — Multi-Agent + Memory + Self-Evolution — Design Spec

> **Date**: 2026-06-28
> **Status**: Draft for review
> **Builds on**: v1.0.0 (plan-first) — `docs/superpowers/specs/2026-06-27-nexus-plan-first-redesign-design.md`
> **Scope**: Nexus v1.1 — sub-agent roles, memory layer, self-evolution feedback loop
> **Goal**: 让 Nexus 在 **autonomy（自主深度）+ specialization（角色分工）+ memory（跨会话学习）** 三个维度超越 Claude Code

---

## 1. 背景与动机

### 1.1 v1.0 现状（截至 2026-06-27）

| 维度 | 数据 |
|---|---|
| 代码规模 | ~2.2k LOC 计划-执行核心 + ~285k LOC 既有 harness 模块（未接入）|
| 测试覆盖 | 107 tests passing（agent/tools/tui/integration/wal/cli/llm-smoke）|
| 已有能力 | Plan first-class / TUI 编辑 / WAL 回放 / 8 工具 / 暂停-恢复 |
| 缺失能力 | sub-agent 多角色 / 长期记忆 / 自我进化 / 失败后自动修复 |

### 1.2 v1.1 目标：让 Nexus 比 Claude Code 更聪明

Claude Code 的短板：
- **没有 sub-agent**：一个通用 agent 扛所有任务；复杂任务容易跑偏
- **没有跨会话记忆**：每次新开会话都从零开始；不学习用户偏好
- **失败后盲重试**：on_failure=retry 不读 verifier 反馈；用户被打断频率高
- **没有计划质量反馈环**：plan 完成就完成，不改进 prompt

Nexus v1.1 的差异化能力：

| 维度 | Claude Code | Nexus v1.1 |
|---|---|---|
| 角色分工 | 单一通用 agent | Specifier / Implementer / Reviewer / Security 4 角色，可组合 |
| 长期记忆 | 每次会话独立 | WAL 派生的 episodic 索引 + 项目级 semantic 索引 + 可重用 skill 库 |
| 失败恢复 | blind retry / 问用户 | `retry_with_feedback` 把 verifier 错误喂回 LLM 自纠 |
| Plan 质量改进 | 静态 prompt | Evolver 读 WAL 错误模式，staged prompt 更新 + 用户审批门 |
| 验证 | 弱（仅 LLM-as-judge） | 注册化 verifier pipeline（security / tdd / test / review） |

### 1.3 范围与非目标

**做**：
- Sub-agent wiring：复用既有 `src/agents/` 角色文件，新增 `SUBPLAN` step kind
- Memory 三层（working / episodic / semantic / skill）
- Self-evolution 闭环（Evolver + PromptTemplateRegistry + 用户审批门）
- Verification pipeline 接入（复用 `src/verification/`）
- WAL v2 格式 + 迁移脚本（forward-compat，v1.0 plans 仍可读）
- 新 CLI 命令 + 新 TUI panels

**不做**：
- 并行 multi-agent speculation（v3 TaskForest 范畴）
- MCP server/client（v1.2+）
- 模型路由器（multi-provider 抽象）— v1.1 仍只支持 Anthropic
- 自动 embedding（opt-in via `[embeddings]` extra；默认 substring search）

---

## 2. 核心理念

**Sub-agent 的输出是 Plan，不是字符串。** 这是整个 v1.1 的关键设计决策——它把 legacy `src/agents/` 的 `execute(task) -> AgentResult(output: str)` 改造成 `delegate_task(role, task) -> Plan`，而无需改动任何一个 role 文件。

具体做法：在 `RoleRegistry.spawn()` 里把 role 的 `system_prompt` 注入到子 plan 的 Planner，把 role 的 `tools` 作为子 plan 的 tool 过滤器，然后调用 `runtime.plan()` 生成子 Plan，再用 `runtime.walk()` 执行。Role 文件不知道也不需要知道这一切；它们只是被动提供配置。

**WAL 是唯一真理来源。** Memory 不另起存储层；episodic index 是 WAL JSONL 的派生视图（cached at `.nexus/memory/`），semantic index 可选，skill 库来自既有 `src/skills/loader.py`。这样保持 compat B 简单（升级 WAL 头格式即可），避免三套数据源同步。

**用户掌握进化权。** Evolver 不会自动应用 prompt 改动——每个变更 staged，用户在 TUI 看到建议（"Evolver 想加 'pytest 前先 lint' 到 verifier.test_gate.rules，approve?"），可以 approve / reject / revert。`--auto-evolve` flag 留给高级用户。

---

## 3. 架构总览

### 3.1 顶层数据流

```
User task
   │
   ▼
CLI / TUI ──► AgentRuntime (existing)
                │
                ├─ plan() ──► Planner ──► Plan (with SUBPLAN steps)
                │                ▲
                │                │ injects context from:
                │                │   - EpisodicIndex (similar past plans)
                │                │   - SemanticIndex (project conventions)
                │                │   - PromptTemplateRegistry (current prompt versions)
                │
                └─ walk() ──► PlanWalker
                              │
                              ├─ TOOL     ──► ToolRegistry
                              ├─ VERIFY   ──► VerificationAdapter ──► src/verification/pipeline
                              ├─ CRITIQUE ──► LLM
                              ├─ ASK_USER ──► ControlChannel
                              └─ SUBPLAN  ──► RoleRegistry.spawn() ──► runtime.plan() + walk() (recursive)
                                                │
                                                ▼
                                            (sub-plan events flow through SAME ControlChannel)

Post-walk hook:
   walk() complete (success or failure)
      │
      ▼
   Evolver.record_outcome(plan, step_results)
      │  reads WAL + step results + EpisodicIndex
      │  computes error histograms, retry-rates, planner-failures
      │
      ▼
   Evolver.update_prompt_registry()
      │  stages changes to PromptTemplateRegistry
      │  user approves in TUI before they take effect
      │
      ▼
   Next plan() consults updated prompts ──► loop closes

Context (WALManager + MemoryStore):
   - WALManager  (existing, unchanged) — JSONL append-only checkpoint
   - MemoryStore (NEW)
        ├─ EpisodicIndex  : Plan → outcome (derived from WAL)
        ├─ SemanticIndex  : convention/chunk → terms (opt-in embeddings)
        └─ SkillIndex     : wraps src/skills/loader.py
```

### 3.2 关键不变式（v1.0 → v1.1 不破）

| 不变式 | 如何维持 |
|---|---|
| Plan 是 first-class、可编辑 artifact | SUBPLAN 子计划由 runtime 自动生成；TUI 平铺显示父+子步骤，但用户编辑的依然是父 Plan |
| 仅在 step 边界暂停 | SUBPLAN 是父 plan 的一个 step；父 step 的边界就是子 plan 的整体边界；暂停对父生效即对子生效 |
| WAL JSONL append-only | 加 `format_version` 头（line 0）；v1.0 records 可读；migration 脚本可选归一化到 v2 |
| 所有事件走 ControlChannel | 子 plan 用同一个 channel；不新增事件类型 |
| `on_failure` 默认 `"ask"` | SUBPLAN 子 plan 的失败 bubble up 成父 step 的 StepFailed；父的 on_failure 仍生效 |

---

## 4. 数据模型

### 4.1 Plan schema 增量改动（v1 → v2）

```python
# src/agent/plan.py (additions only — additive enum value + 2 new optional fields)

class PlanStepKind(str, Enum):
    TOOL = "tool"
    VERIFY = "verify"
    CRITIQUE = "critique"
    ASK_USER = "ask_user"
    SUBPLAN = "subplan"           # NEW in v1.1


@dataclass
class PlanStep:
    # ... v1 fields unchanged ...
    role: AgentRole | None = None            # NEW: only when kind=SUBPLAN
    subplan_args: dict[str, Any] | None = None  # NEW: context passed to sub-planner

    # VERIFY step extension
    pipeline: str | None = None              # NEW: name of verification pipeline
    pipeline_args: dict | None = None        # NEW: pipeline-specific config


class OnFailure(str, Enum):
    ABORT = "abort"
    SKIP = "skip"
    RETRY = "retry"
    ASK = "ask"
    RETRY_WITH_FEEDBACK = "retry_with_feedback"  # NEW: feed verifier errors back to LLM
```

### 4.2 WAL JSONL v2 format

```jsonl
{"format_version": 2, "kind": "wal_header", "created_at": "2026-07-15T...", "nexus_version": "1.1.0"}
{"format_version": 2, "kind": "plan_start", "plan_id": "p_abc12345", "version": 3, "plan": {...}}   ← NEW: full plan persisted
{"format_version": 2, "kind": "step_complete", "cursor": "step-3", "result": {...}, "metadata": {"subplan_result": {...}, "verifier_outcome": {...}}}
```

**Compat B mechanics:**
- v1.0 records (without `format_version` key) load fine in v1.1; v1.0 records lack `metadata` blocks but that's OK—filled lazily on next walk
- v1.1 records in v1.0 reader: `json.loads` silently ignores unknown fields, so step records still load (only `metadata` block is dropped)
- Migration: `nexus session migrate <plan_id>` reads v1 WAL, reconstructs Plan from step records, writes v2 WAL with `_v2` suffix; original WAL untouched; idempotent

### 4.3 RoleDefinition / RoleRegistry

```python
# src/agents/registry.py (NEW, ~80 LOC)

@dataclass
class RoleDefinition:
    role: AgentRole
    system_prompt: str              # injected into sub-plan's Planner
    allowed_tools: list[str]        # ToolRegistry filter for sub-plan
    model_tier: ModelTier           # FAST / SONNET / OPUS
    max_subplan_steps: int = 10     # cap on sub-plan size
    on_subplan_failure: OnFailure = OnFailure.ASK  # parent inherits by default


class RoleRegistry:
    def __init__(self, runtime: AgentRuntime): ...
    def register(self, role: AgentRole, definition: RoleDefinition) -> None: ...
    def spawn(self, role: AgentRole, task: str, context: dict) -> Plan: ...
    def list_roles(self) -> list[AgentRole]: ...

    @classmethod
    def with_defaults(cls, runtime: AgentRuntime) -> "RoleRegistry":
        """Register Specifier/Implementer/Reviewer/Security with sane defaults."""
```

**Default role mapping**（introspection over existing role files）：

| Role | system_prompt | allowed_tools | model_tier | max_steps |
|---|---|---|---|---|
| SPECIFIER | `src/agents/specifier.py` derived | Read, Glob, Grep | SONNET | 8 |
| IMPLEMENTER | `src/agents/implementer.py` derived | Read, Write, Edit, Bash, Glob, Grep | SONNET | 12 |
| REVIEWER | `src/agents/reviewer.py` derived | Read, Glob, Grep, Git | SONNET | 6 |
| SECURITY | `src/agents/security.py` derived | Read, Glob, Grep | FAST | 4 |

### 4.4 Memory data model

```python
# src/context/memory.py (NEW, ~150 LOC)

@dataclass
class EpisodicEntry:
    plan_id: str
    plan_hash: str            # sha256 of canonicalized Plan
    task: str
    outcome: Literal["success", "failed", "aborted"]
    duration_s: float
    step_count: int
    failed_step_ids: list[str]
    error_categories: list[str]   # from src/engine/error_isolation.py
    created_at: datetime


@dataclass
class SemanticEntry:
    chunk_id: str
    path: Path
    start_line: int
    end_line: int
    content: str
    embedding: list[float] | None = None   # only if embeddings enabled


class EpisodicIndex:
    """Derived view over WAL — never writes, only reads + caches."""
    def rebuild(self) -> None: ...                          # scan WAL, rebuild cache
    def similar_past(self, task: str, k: int = 5) -> list[EpisodicEntry]: ...
    def success_rate(self, error_category: str) -> float: ...


class SemanticIndex:
    """Optional semantic memory — embeddings are opt-in."""
    def index_file(self, path: Path) -> None: ...
    def search(self, query: str, k: int = 5) -> list[SemanticEntry]: ...


class SkillIndex:
    """Wraps existing src/skills/loader.py."""
    def suggest(self, task: str, plan: Plan) -> list[Skill]: ...
    def apply(self, skill: Skill, step: PlanStep) -> PlanStep: ...


class MemoryStore:
    """Coordinates all three indexes + WAL sync."""

    def __init__(self, wal: WALManager, project_root: Path, *,
                 embedding_fn: Callable | None = None,
                 skill_loader: SkillLoader | None = None): ...

    def warm(self) -> None: ...
    def episodic(self) -> EpisodicIndex: ...
    def semantic(self) -> SemanticIndex: ...
    def skills(self) -> SkillIndex: ...
    def planner_context(self, task: str, k: int = 5) -> str:
        """Render memory as context block to inject into Planner prompt."""
```

### 4.5 PromptTemplateRegistry

```python
# src/agent/prompts.py (NEW, ~80 LOC)

@dataclass
class PromptTemplate:
    name: str
    system_prompt: str
    version: int
    updated_at: datetime
    source_episodes: list[str]    # plan_ids that influenced this version


class PromptTemplateRegistry:
    def __init__(self, path: Path): ...
    def get(self, name: str) -> PromptTemplate: ...
    def update(self, name: str, template: PromptTemplate) -> None: ...
    def history(self, name: str) -> list[PromptTemplate]: ...
    def revert(self, name: str, version: int) -> None: ...
```

Stored at `.nexus/prompts/{name}.json`. History append-only (one file per template, each line is `{version, system_prompt, updated_at, source_episodes, last_updated_walk_count}` JSONL); revert writes a new version that copies the target version's prompt and resets `last_updated_walk_count`.

### 4.6 VerificationAdapter

```python
# src/agent/verify_adapter.py (NEW, ~70 LOC)

class VerificationAdapter:
    """Bridge between VERIFY step and src/verification/pipeline.py."""

    def __init__(self, wal: WALManager): ...
    def register(self, name: str, pipeline: VerificationPipeline) -> None: ...
    def list_pipelines(self) -> list[str]: ...

    async def run(self, step: PlanStep, step_result: StepResult,
                  ctx: WalkContext) -> VerificationOutcome: ...
```

Default pipeline registrations：

| Pipeline name | Module | Use case |
|---|---|---|
| `security` | `src/verification/security_scan.py` | Scan edited files for unsafe patterns (eval, hardcoded secrets, SQL injection, path traversal) |
| `tdd` | `src/verification/tdd_gate.py` | Verify failing test was added before implementation |
| `test` | `src/verification/test_gate.py` | Run pytest, parse results, fail if any test fails |
| `review` | `src/verification/review_gate.py` | LLM-as-judge on diff against `success_criteria` |

### 4.7 Evolver

```python
# src/agent/evolution.py (NEW, ~120 LOC)

class Evolver:
    """Thin coordinator over src/engine/self_evolution.py + feedback_loop_integration.py."""

    def __init__(self, wal: WALManager, memory: MemoryStore,
                 feedback: FeedbackLoop): ...

    def record_outcome(self, plan: Plan, results: list[StepResult]) -> None: ...
    def update_prompt_registry(self, registry: PromptTemplateRegistry) -> StagedChanges: ...
    def should_replan(self, partial_results: list[StepResult]) -> bool: ...   # dynamic replan signal


@dataclass
class StagedChanges:
    """Evolver-produced prompt updates pending user approval."""

    changes: dict[str, PromptTemplate]   # name → proposed new version
    rationale: dict[str, str]            # name → why this change
    created_at: datetime
```

`StagedChanges` lives at `.nexus/prompts/staged.json` until approved/rejected. TUI shows a modal; user picks per-change approve/reject.

---

## 5. Walker 行为变更

### 5.1 `_execute_step` dispatch (additive branch)

```python
async def _execute_step(self, step: PlanStep) -> StepResult:
    if step.kind == PlanStepKind.SUBPLAN:
        return await self._execute_subplan(step)
    # ... v1 branches (TOOL / VERIFY / CRITIQUE / ASK_USER) unchanged ...

async def _execute_subplan(self, step: PlanStep) -> StepResult:
    sub_plan = self._role_registry.spawn(step.role, step.tool, step.subplan_args)
    try:
        return await self._runtime.walk(sub_plan)
    except PlanAborted as e:
        # Sub-plan abort becomes a normal StepFailed for the parent
        return StepResult(
            status="failed",
            error=str(e),
            metadata={"subplan_aborted": True, "subplan_id": sub_plan.id},
        )
```

### 5.2 VERIFY step extension

```python
async def _execute_verify(self, step: PlanStep, prior_result: StepResult) -> StepResult:
    if step.pipeline:
        outcome = await self._verify_adapter.run(step, prior_result, self._ctx)
        if not outcome.passed:
            if step.on_failure == OnFailure.RETRY_WITH_FEEDBACK:
                # Augment next attempt with verifier errors
                return StepResult(
                    status="retry_with_feedback",
                    feedback=outcome.errors,
                )
            return StepResult(status="failed", error="\n".join(outcome.errors))
        return StepResult(status="verified", metadata={"verifier_outcome": outcome})
    # ... v1 success_criteria-based logic unchanged ...
```

### 5.3 Pause / abort propagation

| Scenario | Behavior |
|---|---|
| User pauses during sub-plan step | `ControlChannel._pause_event` is singleton; pause applies to whole runtime. Parent's "after step N" check blocks; sub-plan step N's `wait_if_paused` also blocks. Single pause dialog visible. |
| User aborts during sub-plan step | `PlanAborted` raised in sub-plan's walker; parent's `_execute_subplan` catches it, returns `StepResult(status="failed", metadata={"subplan_aborted": True})`; parent's `on_failure` strategy applies normally |
| Sub-plan ASK_USER step | Sub-plan's walker blocks waiting for ANSWER_QUESTION command; same channel; user sees the question (with `↳` prefix indicating sub-plan origin) |
| Crash mid-sub-plan | WAL records outer step as `in_progress`; on replay, outer cursor auto-skips entire sub-plan step (sub-step results not replayed individually—would require v1.2 sub-step WAL granularity) |

---

## 6. Planner 行为变更

### 6.1 Prompt augmentation pipeline

```
Planner.plan(task)
   │
   ▼
Build Planner system prompt by concatenating:
   1. Base prompt (v1 unchanged)
   2. EpisodicIndex context block (top-5 similar past plans + their outcomes)
   3. SemanticIndex context block (top-5 project conventions / relevant chunks)
   4. SkillIndex suggestions (top-3 applicable skills)
   5. PromptTemplateRegistry.get("planner") (latest approved version)
   │
   ▼
LLM call (with augmented prompt) ──► JSON response ──► Plan (validated)
```

### 6.2 Default verifier pipeline attachment

The Planner (with augmented prompts) attaches pipelines based on heuristics：

| Step kind | Default pipeline |
|---|---|
| Edit / Write | `security` (post-write scan) |
| Tool step that modifies Python code | `tdd` (verify test added) |
| Step with `success_criteria` mentioning "tests pass" | `test` |
| Final step of any plan | `review` (LLM-as-judge) |

User can override per-step in TUI.

### 6.3 SUBPLAN generation

When the Planner decides a sub-task warrants a specialized role (e.g., "this is a security review → spawn SECURITY role"), it generates a `SUBPLAN` step：

```json
{
  "kind": "subplan",
  "role": "SECURITY",
  "tool": "scan the auth flow in src/auth/ for known vulnerability patterns",
  "subplan_args": {"scope": "src/auth/", "depth": "thorough"},
  "on_failure": "ask",
  "success_criteria": "verifier.security passes with no HIGH or CRITICAL findings"
}
```

---

## 7. CLI 表面

```
# v1.0 unchanged
nexus run --task "..."
nexus tui
nexus session list
nexus session resume <id>

# v1.1 additions
nexus session migrate <id>                 # WAL v1 → v2
nexus role list                             # registered AgentRoles
nexus role show <role>                      # system_prompt + allowed_tools + tier
nexus memory warm                           # force rebuild episodic + semantic indexes
nexus memory stats                          # entry counts, last WAL sync, hits/misses (hit = planner_context() found ≥1 similar past plan with same outcome category)
nexus memory search <query>                 # semantic search
nexus skill list                            # loaded skills
nexus skill apply <name> --step N           # attach skill to step N in current plan
nexus prompt list                           # registered prompt templates + versions
nexus prompt show <name>                    # current version + history
nexus prompt revert <name>@<ver>            # roll back prompt template
nexus evolve --auto                         # auto-approve evolver suggestions (off by default)
```

All new commands follow existing Typer patterns in `src/cli/`.

---

## 8. TUI 表面

### 8.1 Layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Plan (30%)        │ Execution (35%)              │ Verifier (15%)           │
│  ▾ plan_abc       │  ▶ Step 1/4: Read config     │  Last: security ✓        │
│   ▶ Read config   │  → Read({'path': 'config'})  │  Last: test ✗            │
│   ✓ Update value  │  ✓ Read done                 │   test_foo FAILED        │
│   ▸ SUBPLAN       │  ↳ SpecifierAgent sub-plan   │   assert X == Y          │
│     ↳ ...flattened│    ▶ Step 1.1: Draft spec    │  (press v to expand)     │
│   ▸ VERIFY (test) │                              │                          │
│                   ├─────────────────────────────────────────────────────────┤
│                   │ Tool Output (10%)            │ Memory (10%)             │
│                   │ → Read                       │ 3 past plans matching    │
│                   │ args: {'path': 'config'}     │ "add comment"            │
│                   │                              │ 2/3 succeeded            │
└────────────────────────────────────────────────────────────────────────────┘
```

### 8.2 New bindings

| Key | Action | Notes |
|---|---|---|
| `V` | Focus VerifierPanel | |
| `M` | Focus MemoryPanel | |
| `s` | Open SkillPicker modal | Attach skill to focused step |
| `E` | Open EvolveApproval modal | When evolver has staged prompt changes |
| `Ctrl-r` | Re-run focused verifier | No walk required |

### 8.3 New modals

- `VerifierPanel` (always-visible pane)
- `MemoryPanel` (always-visible pane)
- `SkillPickerModal` (on `s`)
- `EvolveApprovalModal` (on `E`, lists staged changes per-prompt)
- `PromptHistoryViewerModal` (within `EvolveApprovalModal`, shows per-template diff)

### 8.4 Plan tree rendering of SUBPLAN

- Collapsed: `▸ SUBPLAN (SpecifierAgent) — "spec the new feature"`
- Expanded: sub-plan steps indented 2 spaces, prefixed `↳`
- Cursor (`j`/`k`) navigates flattened list
- `Enter` toggles expansion

---

## 9. 失败模式

| Failure | Detection | Recovery |
|---|---|---|
| Crash mid-walk | Process restart + WAL replay | `NexusApp.on_mount` checks `wal.recover()`; RecoverModal (v1.0) plus v1.1 detects v1 WAL and offers migration |
| Step timeout | `asyncio.wait_for` (existing) | StepFailed → on_failure |
| Tool raises exception | try/except in `_execute_tool_step` (existing) | StepFailed → on_failure |
| LLM returns invalid JSON | JSON parse failure in Planner (existing) | Retry up to `max_retries` (default 3) |
| Sub-plan aborts | `_execute_subplan` catches `PlanAborted` | Returns StepFailed with `subplan_aborted=True`; parent's on_failure applies |
| Sub-plan needs user input | Sub-plan's walker blocks on ANSWER_QUESTION | Same ControlChannel; TUI shows `↳` prefix |
| WAL corrupted | JSON parse error on read | `MemoryStore.warm()` raises; fallback to empty memory; log warning |
| Embedding model unavailable | `SemanticIndex` checks `embedding_fn is None` | Substring search only; no error |
| Evolver produces bad prompt | Approval gate + schema validation | User rejects; revert via `nexus prompt revert` |
| Memory cache stale vs WAL | Cache has `last_wal_mtime`; on mismatch, `rebuild()` | Auto-rebuild on warm() |
| Feedback loop infinite churn | `Evolver` cap: each `(template_name, version)` pair updates at most once per 5 walks; tracked via `last_updated_walk_count` field on `PromptTemplate` | Skip update if too recent |
| Compat B breaks a v1.0 user | WAL writes are append-only; v1.1 reads v1 WAL like v1.0 did | Migration is opt-in |
| Migration fails | `nexus session migrate` catches JSON errors | Refuses to write `_v2`; reports line number |
| Verifier hangs | `asyncio.wait_for(pipeline.run(), timeout=verifier_timeout_s)` | StepFailed with timeout error |

---

## 10. 测试策略

### 10.1 Coverage targets

| Layer | Tests | Coverage target |
|---|---|---|
| Unit — RoleRegistry, MemoryStore, VerificationAdapter, Evolver, PromptTemplateRegistry | Each module isolated; mock dependencies | 90% line coverage per new file |
| Unit — SUBPLAN walker dispatch, retry_with_feedback on_failure | 8 tests covering happy path + each failure mode + abort propagation + WAL replay | 100% branch coverage on `_execute_subplan` |
| Integration — AgentRuntime + PlanWalker + RoleRegistry + EpisodicIndex (real WAL) | 6 tests: spawn sub-plan, sub-plan failure bubbles to parent on_failure, replay with SUBPLAN cursor, memory injection into Planner prompt, evolver feedback loop end-to-end, migration v1→v2 | Each scenario: assertion on final WAL contents + final Plan state |
| Migration — round-trip v1 WAL → v2 → replay | 3 tests with synthetic v1 WAL files | All known v1 shapes pass |
| TUI — new panels (Verifier, Memory, EvolveApproval, SkillPicker) | 7 tests using Textual `app.run_test()` | Each panel renders + key bindings fire |
| LLM smoke — real `ANTHROPIC_API_KEY` | 4 tests: multi-agent plan runs, memory injection actually changes planner output, evolver produces a prompt update, verifier-driven retry succeeds | Skipped without API key |
| Backward compat — load every existing v1.0 test fixture as v1.1 | 1 test iterating `tests/fixtures/wal_v1/*.jsonl` | All load + resume correctly |

### 10.2 Total test target

107 (v1.0 baseline) + ~35 new = **~142 tests**.

### 10.3 v1.1 release criteria (Definition of Done)

1. All 142 tests pass; coverage ≥85% on new modules
2. Three new TUI panels render correctly under `app.run_test()`
3. Migration round-trip succeeds on every v1 fixture
4. LLM smoke test #1 (multi-agent plan runs end-to-end) passes with API key
5. README, ARCHITECTURE, ROADMAP updated for v1.1 (compat B, new CLI, new panels)
6. CHANGELOG entry summarizing per-phase changes
7. v1.1 git tag, GitHub release notes

---

## 11. 实施阶段

| Phase | Goal | Tasks | Est. | Dependencies |
|---|---|---|---|---|
| **A. Role wiring (B)** | SUBPLAN step + RoleRegistry + walker dispatch | 5 | 4 days | None |
| **B. Verification hooks** | VERIFY pipeline binding + retry_with_feedback + VerifierPanel | 4 | 3 days | A (needs walker extension pattern) |
| **C. Memory layer (C)** | MemoryStore + EpisodicIndex + SemanticIndex + SkillIndex + Planner injection | 8 | 6 days | A, B (verifier outcomes go into memory) |
| **D. Self-evolution** | Evolver + PromptTemplateRegistry + post_walk_hook + approval gate | 6 | 5 days | A, C (Evolver reads EpisodicIndex) |
| **E. Schema + CLI/TUI** | WAL v2 + migrate command + new CLI commands + new TUI panels + new keybindings | 6 | 4 days | A–D (need new artifacts to expose) |
| **F. Tests + release** | Unit + integration + migration + TUI + LLM smoke + README/ROADMAP/ARCHITECTURE rewrite + v1.1 tag | 6 | 4 days | All above |

**Total: 35 tasks, ~26 working days (5 weeks at sustainable pace).**

Critical path: **A → B → C → D → E → F** (all 6 phases sequential due to upstream artifact dependencies). B feeds verifier outcomes into C; C feeds episodic index into D; D produces artifacts that E exposes via CLI/TUI.

---

## 12. 风险与缓解

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Legacy role files don't compose well with new planner prompts | Medium | Medium | Adapter layer introspects only public attrs; if a role needs custom logic, register a custom `RoleDefinition` instead |
| Semantic index embedding model adds heavy dep | Low | Low | Opt-in via `pip install nexus-cc[embeddings]`; default = substring search |
| Evolver produces degenerate prompts (e.g., empty system prompt) | Medium | High | Approval gate mandatory; schema-validate every prompt update; refuse if length < 50 chars |
| Sub-plan WAL replay ambiguous | Medium | Low | Outer cursor only; sub-step results opaque unless explicit replay requested (v1.2 feature) |
| Compat B breaks a v1.0 user mid-upgrade | Low | High | WAL writes are append-only; v1.1 reads v1 WAL exactly like v1.0 did; migration is opt-in |
| LLM smoke tests flaky due to model behavior | Medium | Medium | Use `model_tier=FAST` for smoke tests where possible; tolerate single-shot failures with retry; skip without API key |
| TUI panel rendering breaks at small terminal sizes | Low | Low | Min-width assertion in tests; degrade to stacked layout below 120 cols |
| WAL file grows unbounded | Medium | Medium | Auto-prune entries older than 30 days for plans with status `completed` or `aborted` (queried via `wal.list_plans(status_in=...)`); active plans (status `walking`) keep all entries |
| Sub-plan step explosion (Planner generates huge sub-plan) | Low | Medium | `max_subplan_steps` per role; sub-plan rejected if exceeds cap |
| Feedback loop promotes a bad prompt that breaks future plans | Low | High | Approval gate + revert command + per-template rollback history |

---

## 13. 未来扩展（v1.2+，超出本文档范围）

- 并行 multi-agent speculation（v3 TaskForest + TaskGraph）
- MCP server/client 接入（暴露 nexus.plan / nexus.walk）
- 多 provider 模型路由器（OpenAI / Ollama / SCNET）
- 自动 embedding 默认开启（基于本地 sentence-transformers）
- Sub-plan WAL 细粒度回放（cursor 层级穿透到 sub-step）
- Plan diff visualization（TUI 中可视化 plan 编辑前/后差异）

---

## 14. 参考

- v1.0 design spec: `docs/superpowers/specs/2026-06-27-nexus-plan-first-redesign-design.md`
- v1.0 architecture: `ARCHITECTURE.md`
- v1.0 roadmap: `ROADMAP.md`
- Legacy modules to reuse:
  - `src/agents/` — role definitions
  - `src/verification/` — verification pipelines
  - `src/engine/self_evolution.py` — evolution engine
  - `src/engine/feedback_loop_integration.py` — feedback loop
  - `src/engine/error_isolation.py` — error categorization
  - `src/skills/loader.py` — skill loading
  - `src/engine/context_slice.py` — context management primitives