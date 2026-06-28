# Nexus v1.2 — Model Router — Design Spec

> **Date**: 2026-06-28
> **Status**: Draft for review
> **Builds on**: v1.1.0 (multi-agent + memory + self-evolution) — `docs/superpowers/specs/2026-06-28-nexus-v11-multi-agent-memory-design.md`
> **Scope**: Nexus v1.2 — per-call-site model selection (Haiku 4.5 / Sonnet 4.6 / Opus 4.8) + cost telemetry
> **Goal**: 让 Nexus 在 **cost-per-task（每任务成本）+ capability-matching（能力匹配）+ observability（可观测）** 三个维度上比 v1.1 更经济、可审计

---

## 1. 背景与动机

### 1.1 v1.1 现状（截至 2026-06-28）

| 维度 | 数据 |
|---|---|
| LLM 调用方 | Planner、CRITIQUE step、ReviewGate delegate、SUBPLAN 子 Planner、Evolver（间接） |
| 默认模型 | 全部走 Sonnet 4.6 (`claude-sonnet-4-6` / `claude-sonnet-4-20250514`) |
| 模型选择逻辑 | `src/cli/commands/run.py::_build_llm_client` 固定 `ANTHROPIC_MODEL` env var；RoleRegistry 的 `model_tier` 字段**当前未被任何代码读取** |
| 现有 router | `src/llm/model_router.py::ModelRouter` 存在，支持 task-type→model 选择；但**未被接入** v1.1 主调用链 |
| cost 命令 | `nexus cost` 是 stub（"Phase 2 feature"） |
| token 计量 | `LLMClient` 内部已累加 `total_input_tokens` / `total_output_tokens`，但**不持久化、不聚合、不暴露** |

### 1.2 为什么需要 model router

> **Default mapping note (2026-06-28, finalized):** The one deliberate cost-downgrade in v1.2 is `VERIFIER_SECURITY` → `claude-haiku-4-5` (vs v1.1's all-Sonnet-4.6). Binary pattern-match review tolerates the ~12× cost reduction; capability delta is negligible for that call site. All other hints (`PLANNER`, `CRITIQUE`, `VERIFIER_REVIEW`, `EVOLVER`, `DEFAULT`) remain on `claude-sonnet-4-6` — matching v1.1 behavior, so existing plans replay unchanged. See §5.1 (`DEFAULT_POLICY` table) and §4.2 (`ModelPolicy.DEFAULT_POLICY`).

v1.1 把所有 LLM 调用都钉死在 Sonnet 4.6，导致三类浪费：

1. **Verifier review 贵**：ReviewGate 的 spec-compliance / logic-analysis delegate 是 pass/fail 二元判断，用 Sonnet 过度。Haiku 4.5 成本约为 Sonnet 的 **1/12**，准确率损失对 binary pass/fail 任务影响可忽略。
2. **Evolver 短 prompt 也用 Sonnet**：Evolver 增量修改 prompt 模板（几十 token 输入），用 Opus 都不过分，但 Sonnet 在这里没有 capability 优势。
3. **Planner 重 prompt 也用 Sonnet**：包含 episodic / semantic / skill context 的 Planner prompt 已经可能 20k+ tokens，对于"复杂分布式系统设计"类 deep plan，Sonnet 可能规划出有缺陷的 step 顺序，但 Opus 4.8 更可能做出全局正确的拓扑决策。

### 1.3 v1.2 目标：让 Nexus 像 multi-tier storage 一样分层

| 维度 | v1.1 | v1.2 |
|---|---|---|
| 默认模型 | 全 Sonnet 4.6 | 按 call site 路由 |
| 模型选择 | env var 单一 | call site hint + per-role config + policy table |
| 成本可见性 | 内部累加，不暴露 | WAL 持久化每调用 token + USD；`nexus cost` 聚合 |
| 路由策略 | 无 | policy.yaml + 4 call-site overrides |
| Backwards compat | — | 默认值 = Sonnet 4.6（与 v1.1 行为一致） |

### 1.4 范围与非目标

**做**：
- 接入既有 `src/llm/model_router.py` 到主调用链
- 新增 `src/llm/cost.py`：token 持久化、聚合、CLI 暴露
- 4 个 call-site hint：`Planner`、`Role agent SUBPLAN`、`Verifier ReviewGate delegate`、`Evolver`（间接通过 Planner）
- `nexus cost` 子命令：`today` / `by-role` / `by-model`
- WAL v2 cost record（增量字段，不破 compat）
- 1 个新 enum + 1 个新 dataclass + 1 个新 module

**不做**：
- 并行 multi-model speculation（v3 TaskForest 范畴）
- 自动 budget 限制 / token cap（v1.3）
- OpenAI / Ollama provider 启用（v1.2 仍仅 Anthropic 默认；router infrastructure 预留接口）
- Opus 4.8 默认启用（仅在 `--deep` 标志或 Plan hint 时升级）
- 自动检测 plan 复杂度（v1.3 heuristic；v1.2 显式 hint）

---

## 2. 核心理念

**Model 选择是 explicit 的，不是 implicit 的。** v1.2 在每个 LLM call site 显式声明一个 `ModelHint`，router 据此查 policy 表，得到具体 model name。Planner 的 prompt augmentation 路径、RoleRegistry 的 `model_tier` 字段、CLI 的 `--model` flag 三者最终都解析成同一个 `ModelHint`。这避免 v1.1 那种"RoleDefinition.model_tier 字段定义了但没人读"的死代码。

**Default = current behavior。** 没有 hint 时 router 返回当前默认模型（Sonnet 4.6），确保 v1.1 的所有调用路径行为不变。Backward compat 是设计约束，不是事后补丁。

**Cost telemetry 是 first-class。** 每次 `LLMClient.complete()` 完成后自动 emit 一个 `CostRecord`（in-memory + WAL JSONL append）。不引入新的存储层；cost index 是 WAL 的派生视图，cached at `.nexus/cost/`，与 v1.1 memory index 同一模式。

**Router 是 policy-driven，不是 heuristic-driven。** 路由策略写在 `policy.yaml` 中（per-role defaults + per-call-site overrides + env var fallbacks），不写死在代码里。用户可以编辑 `.nexus/policy.yaml` 自定义 routing；默认值是 safe default。

---

## 3. 架构总览

### 3.1 顶层数据流

```
CLI / TUI
   │
   ▼
AgentRuntime
   │
   ├─ plan() ──► Planner ──► ModelRouter.route(hint=ModelHint.PLANNER)
   │                              │
   │                              ├─ read policy.yaml (per-call-site + per-role defaults)
   │                              ├─ resolve hint → model name
   │                              └─ get_client(model_name) ──► LLMClient
   │                                                              │
   │                                                              ▼
   │                                                          API call
   │                                                              │
   │                                                              ▼
   │                                                          CostRecord (in-memory + WAL)
   │
   └─ walk() ──► PlanWalker
                  │
                  ├─ CRITIQUE ──► ModelRouter.route(hint=ModelHint.CRITIQUE)
                  ├─ SUBPLAN  ──► RoleRegistry.spawn()
                  │                  │
                  │                  ├─ read RoleDefinition.model_tier → ModelHint.ROLE_<TIER>
                  │                  │
                  │                  ▼
                  │              Planner.plan() ──► ModelRouter.route(hint=...)
                  │
                  └─ VERIFY (review pipeline)
                                 │
                                 ▼
                             ReviewGate._delegate_logic_analysis()
                                 │
                                 ▼
                             delegate_task() ──► ModelRouter.route(hint=ModelHint.VERIFIER_REVIEW)

Post-walk hook:
   walk() complete
      │
      ▼
   CostAggregator.flush()  (aggregates session records → .nexus/cost/session_<id>.json)

CLI surface:
   nexus cost today                  # today's totals
   nexus cost by-role                # grouped by ModelHint category
   nexus cost by-model               # grouped by model name
   nexus cost session <plan_id>      # single session breakdown

Context:
   - ModelRouter (refactor existing src/llm/model_router.py)
        ├─ policy.yaml loader
        ├─ hint resolution
        ├─ model registry (subset of existing ModelConfig table)
        └─ LLMClient cache (existing)
   - CostTracker (NEW)
        ├─ in-memory ring buffer (last 1000 records)
        ├─ WAL JSONL append (one record per LLM call)
        └─ CostAggregator (session/role/model rollups, cached at .nexus/cost/)
```

### 3.2 关键不变式（v1.1 → v1.2 不破）

| 不变式 | 如何维持 |
|---|---|
| 所有 LLM 调用经 ModelRouter | `LLMClient` 仍可独立使用；router 是 wrapper，不是 replacement |
| 默认模型 = Sonnet 4.6（v1.1 行为） | `ModelHint` → model resolution 的 default branch 返回 v1.1 model |
| WAL JSONL append-only | cost record 是 new `kind` value；老 record 无 cost 字段，聚合时按 0 处理 |
| Plan / WAL / CLI 命令集全部向后兼容 | 仅新增 `nexus cost` 子命令；`nexus run` 多接 `--model` flag |
| RoleDefinition.model_tier 字段被实际使用 | RoleRegistry.spawn() 读取 → 注入到子 plan 的 Planner hint |
| 控制面（暂停 / 重试 / abort）不受影响 | router 只决定 model name，不介入 control flow |

---

## 4. 数据模型

### 4.1 ModelHint enum（NEW in v1.2）

```python
# src/llm/router.py (NEW, ~30 LOC)

from enum import Enum


class ModelHint(str, Enum):
    """Per-call-site model selection hint.

    Each value identifies a class of LLM call with its own routing policy.
    Resolved by ModelRouter against policy.yaml to produce a concrete model name.

    Values:
        PLANNER          — Planner.plan() in AgentRuntime or sub-plan
        CRITIQUE         — CRITIQUE step (post-step self-review in walker)
        VERIFIER_SECURITY — ReviewGate.security pattern-match delegate (cheap)
        VERIFIER_REVIEW  — ReviewGate spec-compliance + logic-analysis delegate
        EVOLVER          — Reserved for future Evolver prompt-rewriting call (v1.2: not yet emitted)
        DEFAULT          — Any call site without explicit hint; falls back to v1.1 default
    """

    PLANNER = "planner"
    CRITIQUE = "critique"
    VERIFIER_SECURITY = "verifier_security"
    VERIFIER_REVIEW = "verifier_review"
    EVOLVER = "evolver"
    DEFAULT = "default"
```

### 4.2 ModelPolicy（NEW in v1.2）

```python
# src/llm/policy.py (NEW, ~80 LOC)

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.llm.router import ModelHint


@dataclass
class ModelPolicy:
    """Routing policy resolved from .nexus/policy.yaml + env + built-in defaults.

    Attributes:
        defaults: hint → model name (built-in defaults: PLANNER/CRITIQUE/.../DEFAULT)
        per_role: AgentRole → model name (overrides defaults for SUBPLAN roles)
        cli_override: Optional model name from --model CLI flag (highest priority)
        budget_usd_per_session: Optional cap; warn but don't block (v1.2 advisory only)
    """

    defaults: dict[ModelHint, str] = field(default_factory=dict)
    per_role: dict[str, str] = field(default_factory=dict)
    cli_override: str | None = None
    budget_usd_per_session: float | None = None

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
        cli_model: str | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> "ModelPolicy":
        """Resolve policy from 3 sources (priority high → low):
            1. --model CLI flag (cli_override)
            2. NEXUS_MODEL_<HINT> env vars (e.g. NEXUS_MODEL_PLANNER)
            3. .nexus/policy.yaml (defaults + per_role)
            4. built-in DEFAULT_POLICY (fallback)
        """
        policy_yaml: dict = {}
        if project_root is not None:
            path = project_root / ".nexus" / "policy.yaml"
            if path.exists():
                policy_yaml = yaml.safe_load(path.read_text()) or {}

        # Built-in defaults (the "v1.2 default routing table")
        defaults = dict(cls.DEFAULT_POLICY)
        defaults.update(policy_yaml.get("defaults", {}))

        per_role = dict(policy_yaml.get("per_role", {}))

        # Env overrides (e.g. NEXUS_MODEL_PLANNER=claude-opus-4-8-20260601)
        if env_overrides:
            for hint_name, model in env_overrides.items():
                try:
                    hint = ModelHint(hint_name.lower())
                    defaults[hint] = model
                except ValueError:
                    pass  # unknown hint name; ignore

        return cls(
            defaults=defaults,
            per_role=per_role,
            cli_override=cli_model,
            budget_usd_per_session=policy_yaml.get("budget_usd_per_session"),
        )

    # Built-in defaults — the "v1.2 routing table" (Section 5)
    DEFAULT_POLICY: dict[ModelHint, str] = {
        ModelHint.PLANNER: "claude-sonnet-4-6",
        ModelHint.CRITIQUE: "claude-sonnet-4-6",
        ModelHint.VERIFIER_SECURITY: "claude-haiku-4-5",
        ModelHint.VERIFIER_REVIEW: "claude-sonnet-4-6",
        ModelHint.EVOLVER: "claude-sonnet-4-6",
        ModelHint.DEFAULT: "claude-sonnet-4-6",
    }

    def resolve(
        self,
        hint: ModelHint,
        role: str | None = None,
    ) -> str:
        """Resolve hint (+ optional role) to a concrete model name.

        Priority: cli_override > per_role[role] > defaults[hint] > DEFAULT.
        """
        if self.cli_override:
            return self.cli_override
        if role and role in self.per_role:
            return self.per_role[role]
        return self.defaults.get(hint, self.defaults[ModelHint.DEFAULT])
```

### 4.3 ModelRouter（refactor existing src/llm/model_router.py）

```python
# src/llm/router.py (continuation)

from src.llm.client import LLMClient, Provider, Usage


@dataclass
class ModelConfig:
    """Per-model pricing + capability metadata (existing, augmented)."""

    name: str
    provider: Provider
    context_window: int = 200000
    supports_tools: bool = True
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    # NEW v1.2
    aliases: list[str] = field(default_factory=list)  # e.g. ["sonnet", "opus", "haiku"]


class ModelRouter:
    """Routes LLM calls to per-hint models, records cost telemetry.

    Refactor of v1.1 model_router.py:
    - select_model() replaced by route(hint) — explicit, no TaskType heuristic
    - Adds CostTracker dependency (constructor-injected)
    - Preserves existing get_client(model_name) for legacy callers
    """

    # Anthropic model registry (v1.2: subset of v1.1's; OpenAI/Ollama entries dropped from default)
    DEFAULT_MODELS: dict[str, ModelConfig] = {
        "claude-haiku-4-5-20260601": ModelConfig(
            name="claude-haiku-4-5-20260601",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.005,
            aliases=["haiku", "haiku-4.5"],
        ),
        "claude-sonnet-4-6": ModelConfig(
            name="claude-sonnet-4-6",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            aliases=["sonnet", "sonnet-4.6"],
        ),
        "claude-opus-4-8-20260601": ModelConfig(
            name="claude-opus-4-8-20260601",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            cost_per_1k_input=0.015,
            cost_per_1k_output=0.075,
            aliases=["opus", "opus-4.8"],
        ),
    }

    def __init__(
        self,
        *,
        policy: ModelPolicy,
        cost_tracker: "CostTracker | None" = None,
        api_keys: dict[Provider, str] | None = None,
    ):
        self._policy = policy
        self._cost = cost_tracker or CostTracker.noop()
        self._api_keys = api_keys or {}
        self._clients: dict[str, LLMClient] = {}

    def route(
        self,
        *,
        hint: ModelHint = ModelHint.DEFAULT,
        role: str | None = None,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> tuple[str, "Response"]:
        """Resolve hint → model → client → API call → CostRecord emit.

        Returns (model_name, Response).
        """
        model_name = self._policy.resolve(hint, role=role)
        client = self.get_client(model_name)
        response = client.complete(
            messages=messages,
            tools=tools or [],
            system_prompt=system_prompt,
        )
        self._cost.record(
            CostRecord(
                model=model_name,
                hint=hint.value,
                role=role,
                usage=response.usage,
                timestamp=datetime.now(),
            )
        )
        return model_name, response

    def get_client(self, model_name: str) -> LLMClient:
        """Return cached LLMClient for model_name (existing v1.1 behavior)."""
        if model_name in self._clients:
            return self._clients[model_name]
        config = self.DEFAULT_MODELS[model_name]  # raises KeyError if unknown
        client = LLMClient(
            provider=config.provider,
            model=config.name,
            api_key=self._api_keys.get(config.provider),
        )
        self._clients[model_name] = client
        return client
```

### 4.4 CostRecord + CostTracker（NEW in v1.2）

```python
# src/llm/cost.py (NEW, ~150 LOC)

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from src.llm.client import Usage


@dataclass
class CostRecord:
    """One row in the cost ledger — one per LLM API call.

    Persisted to WAL as JSONL with kind="llm_cost".
    """

    model: str               # model name used
    hint: str                # ModelHint value
    role: str | None         # SUBPLAN role if applicable, else None
    usage: Usage             # input/output token counts (already normalized)
    timestamp: datetime
    cost_usd: float = 0.0   # computed from model_config.cost_per_1k_*

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class CostAggregate:
    """Rollup of N CostRecords, keyed by some dimension."""

    key: str                         # dimension value (e.g. model name, hint, role)
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CostTracker:
    """Append-only cost ledger with WAL persistence + in-memory ring buffer."""

    def __init__(self, wal: Any, project_root: Path):
        self._wal = wal
        self._root = project_root
        self._buffer: list[CostRecord] = []     # ring of last 1000 records
        self._index_path = project_root / ".nexus" / "cost" / "index.json"

    def record(self, rec: CostRecord) -> None:
        """Append to ring buffer + WAL JSONL (kind=llm_cost)."""
        self._buffer.append(rec)
        if len(self._buffer) > 1000:
            self._buffer = self._buffer[-1000:]
        # Append to WAL — fails silently if wal is None (e.g. unit tests)
        if self._wal is not None and hasattr(self._wal, "append_cost"):
            self._wal.append_cost(rec)

    @classmethod
    def noop(cls) -> "CostTracker":
        """For unit tests / dry-runs: no WAL, no persistence."""
        return cls(wal=None, project_root=Path("."))

    def aggregate_by(
        self,
        dimension: str,        # "model" | "hint" | "role" | "session"
        session_id: str | None = None,
    ) -> list[CostAggregate]:
        """Compute rollups. Reads from in-memory buffer + WAL scan if needed."""
        records = self._records_for_session(session_id)
        groups: dict[str, CostAggregate] = {}
        for r in records:
            key = getattr(r, dimension) or "unknown"
            agg = groups.setdefault(key, CostAggregate(key=key))
            agg.call_count += 1
            agg.input_tokens += r.usage.input_tokens
            agg.output_tokens += r.usage.output_tokens
            agg.cost_usd += r.cost_usd
        return sorted(groups.values(), key=lambda a: -a.cost_usd)

    def _records_for_session(
        self, session_id: str | None
    ) -> list[CostRecord]:
        """Read records from WAL JSONL, optionally filtered by session."""
        if self._wal is None:
            return list(self._buffer)
        # Reuse existing WALManager.read_records(kind="llm_cost", session_id=...)
        return self._wal.read_cost_records(session_id=session_id)
```

### 4.5 WAL v2 cost record extension（NEW in v1.2）

```jsonl
{"format_version": 2, "kind": "wal_header", "created_at": "2026-06-28T...", "nexus_version": "1.2.0"}
{"format_version": 2, "kind": "plan_start", "plan_id": "p_abc12345", "version": 3, "plan": {...}}
{"format_version": 2, "kind": "step_complete", "cursor": "step-3", "result": {...}, "metadata": {"llm_cost": {...}}}
{"format_version": 2, "kind": "llm_cost", "plan_id": "p_abc12345", "model": "claude-haiku-4-5", "hint": "verifier_security", "role": null, "input_tokens": 1200, "output_tokens": 80, "cost_usd": 0.0016, "timestamp": "2026-06-28T..."}
```

**Compat B mechanics:**
- v1.1 WAL records load fine in v1.2; v1.1 records lack `kind="llm_cost"` rows but that's OK—`CostTracker.aggregate_by()` returns empty groups for sessions without cost records
- v1.2 WAL records in v1.1 reader: `json.loads` silently ignores unknown `kind` values; `llm_cost` records dropped silently on replay (cost is observational, not load-bearing for walk correctness)
- No migration script needed

### 4.6 Plan schema extension（NEW in v1.2）

```python
# src/agent/plan.py (additions only — additive optional fields)


@dataclass
class PlanStep:
    # ... v1.1 fields unchanged ...
    model_hint: ModelHint | None = None        # NEW: explicit hint for this step's LLM call
    model_role: str | None = None              # NEW: only when kind=SUBPLAN; for policy.per_role lookup


@dataclass
class Plan:
    # ... v1.1 fields unchanged ...
    planner_model_hint: ModelHint = ModelHint.PLANNER  # NEW: default hint for sub-planners in this plan
```

---

## 5. 路由策略

### 5.1 Default policy table

```yaml
# .nexus/policy.yaml (default, created on first `nexus run`)

defaults:
  planner:           claude-sonnet-4-6
  critique:          claude-sonnet-4-6
  verifier_security: claude-haiku-4-5
  verifier_review:   claude-sonnet-4-6
  evolver:           claude-sonnet-4-6
  default:           claude-sonnet-4-6

# Per-SUBPLAN-role overrides; matched against RoleDefinition.model_tier mapping:
per_role:
  SPECIFIER:    claude-sonnet-4-6   # explicit; same as default
  IMPLEMENTER:  claude-sonnet-4-6
  REVIEWER:     claude-sonnet-4-6
  SECURITY:     claude-haiku-4-5    # downgraded from v1.1 default
  # Example v1.3+: specifier-deep: claude-opus-4-8

budget_usd_per_session: null   # advisory only in v1.2; warn if exceeded
```

### 5.2 Per-call-site rules

| Call site | Hint | Default model | Rationale | Override mechanism |
|---|---|---|---|---|
| `Planner.plan()` (parent) | `PLANNER` | Sonnet 4.6 | Plan generation needs strong reasoning; not deep enough for Opus | `--deep-plan` flag → Opus 4.8 |
| `Planner.plan()` (SUBPLAN child) | `PLANNER` | per-role lookup → ModelTier mapping → default tier model | Inherits parent's plan policy + role tier | RoleDefinition.model_tier; per_role YAML |
| `RoleDefinition.model_tier=FAST` → SUBPLAN | `PLANNER` + role=FAST | Haiku 4.5 | Security scan is short-prompt / pattern-match | per_role YAML |
| `RoleDefinition.model_tier=SONNET` → SUBPLAN | `PLANNER` + role=SONNET | Sonnet 4.6 | Default | per_role YAML |
| `RoleDefinition.model_tier=OPUS` → SUBPLAN | `PLANNER` + role=OPUS | Opus 4.8 | Deep reasoning for complex specs | per_role YAML |
| `_execute_critique_step` (CRITIQUE) | `CRITIQUE` | Sonnet 4.6 | Post-step self-review needs reasoning | step-level `model_hint` override |
| `ReviewGate._delegate_spec_compliance` | `VERIFIER_REVIEW` | Sonnet 4.6 | Independent spec-check needs reasoning | -- |
| `ReviewGate._delegate_logic_analysis` | `VERIFIER_REVIEW` | Sonnet 4.6 | Independent logic analysis | -- |
| `SecurityScan` (regex/AST, no LLM) | — | — | Currently uses regex + AST only; LLM delegate is future (v1.3) | -- |
| `_execute_verify_v1` success_criteria LLM | `CRITIQUE` | Sonnet 4.6 | -- | step-level `model_hint` override |

### 5.3 ModelTier → ModelHint mapping（NEW in v1.2）

```python
# src/llm/router.py

_MODEL_TIER_TO_MODEL: dict[ModelTier, str] = {
    ModelTier.FAST:   "claude-haiku-4-5",
    ModelTier.SONNET: "claude-sonnet-4-6",
    ModelTier.OPUS:   "claude-opus-4-8",
}
```

`RoleRegistry.spawn()` consumes `definition.model_tier` and passes the resolved model name into `runtime.plan_subplan()` via a new `model_name` arg.

### 5.4 CLI `--model` flag (highest priority)

```
nexus run --task "..." --model claude-opus-4-8        # override EVERYTHING
nexus run --task "..." --model haiku                 # alias resolution
nexus run --task "..." --deep-plan                   # shortcut for planner=Opus only
```

Aliases (`haiku` / `sonnet` / `opus`) resolved via `ModelConfig.aliases` lookup.

### 5.5 Env var overrides (power users)

```bash
NEXUS_MODEL_PLANNER=claude-opus-4-8   # only Planner uses Opus; everything else default
NEXUS_MODEL_VERIFIER_REVIEW=claude-haiku-4-5  # downgrade Verifier to cheap
```

---

## 6. Cost Telemetry

### 6.1 Recording path

```
LLMClient.complete() ──► returns Response(usage=Usage(...))
   │
   ▼
ModelRouter.route() ──► wraps call + emits CostRecord
   │
   ├─ in-memory ring buffer (last 1000)
   └─ WALManager.append_cost(record) ──► JSONL append
                                              │
                                              ▼
                                          .nexus/wal.jsonl
```

`LLMClient` itself is unchanged — it already accumulates `total_input_tokens` / `total_output_tokens` per-instance. The Router is the single point that emits a `CostRecord` per call.

### 6.2 Aggregation path

```
nexus cost today
   │
   ▼
CostTracker.aggregate_by(dimension="hint")
   │
   ├─ scan WAL for kind="llm_cost" since 00:00 today
   ├─ group by hint.value (planner / verifier_review / ...)
   └─ sum tokens + USD, sort by cost_usd DESC
   │
   ▼
Click table output:
   HINT                CALLS    INPUT_TOK    OUTPUT_TOK    COST_USD
   planner             3        12,400       2,100         $0.069
   verifier_review     8        18,200       3,400         $0.105
   verifier_security   4        2,100        400           $0.004
   ──────────────────────────────────────────────────────────────
   TOTAL               15       32,700       5,900         $0.178
```

### 6.3 Per-call-site CostRecord schema

```jsonl
{"format_version": 2, "kind": "llm_cost", "plan_id": "p_abc12345", "session_id": "sess_xyz", "model": "claude-haiku-4-5-20260601", "hint": "verifier_security", "role": null, "input_tokens": 1200, "output_tokens": 80, "cost_usd": 0.0016, "timestamp": "2026-06-28T14:23:11Z", "duration_ms": 412}
```

### 6.4 CLI surface

```
nexus cost today                  # aggregate last 24h, by hint
nexus cost by-model               # group by model name
nexus cost by-role                # group by SUBPLAN role
nexus cost session <plan_id>      # single session, by hint
nexus cost plan <plan_id>         # alias for session (back-compat with v1.1 wording)
nexus cost export --csv           # full ledger as CSV
```

### 6.5 Budget advisory（advisory only in v1.2）

If `policy.budget_usd_per_session` is set and exceeded:
- `CostTracker` emits a warning to stderr at walk-end
- CLI shows banner: "Session cost $X.XX exceeded budget $Y.YY"
- **No blocking** in v1.2; hard cap deferred to v1.3

---

## 7. CLI 表面

```
# v1.1 unchanged
nexus run --task "..."
nexus tui
nexus session list
nexus session resume <id>
nexus role list
nexus role show <role>
nexus memory warm
nexus memory stats
nexus evolve --auto

# v1.2 additions
nexus run --task "..." --model claude-opus-4-8       # global override
nexus run --task "..." --model sonnet                # alias
nexus run --task "..." --deep-plan                   # planner-only Opus upgrade
nexus cost today                                     # today's totals by hint
nexus cost by-model                                  # group by model
nexus cost by-role                                   # group by SUBPLAN role
nexus cost session <plan_id>                         # single session detail
nexus cost export --csv                              # full ledger export
nexus model list                                     # show registered models + aliases + pricing
nexus model show <name|alias>                        # pricing + capability detail
```

All new commands follow existing Click patterns in `src/cli/commands/`.

### 7.1 New files

```
src/llm/router.py           # NEW: ModelHint + ModelRouter (refactor from existing)
src/llm/policy.py           # NEW: ModelPolicy + YAML/env/CLI resolution
src/llm/cost.py             # NEW: CostRecord + CostTracker + CostAggregate
src/cli/commands/cost.py    # REWRITE: implement today's / by-model / by-role / session / export
src/cli/commands/model.py   # NEW: nexus model list/show
```

### 7.2 Modified files

```
src/agent/planner.py        # accept model_hint param (default ModelHint.PLANNER)
src/agent/runtime.py        # construct ModelRouter + CostTracker; pass hint to Planner
src/agent/walker.py         # CRITIQUE step uses hint=ModelHint.CRITIQUE
src/agent/verify_adapter.py # pass hint to ReviewGate delegate
src/agents/registry.py      # RoleRegistry.spawn() reads model_tier, passes model_name to sub-plan
src/agents/default_registry.py  # SECURITY role default → ModelTier.FAST (already there; now actually used)
src/llm/client.py           # no changes (just exposes Usage; Router consumes it)
src/cli/main.py             # register `model` command group
src/cli/commands/run.py     # construct ModelPolicy + ModelRouter; wire --model / --deep-plan flags
```

---

## 8. TUI 表面

### 8.1 Cost panel（NEW in v1.2）

```
┌────────────────────────────────────────────────────────────┐
│ Plan (30%)        │ Execution (35%)       │ Cost (10%)       │
│  ▾ plan_abc       │  ▶ Step 1/4           │ Session: $0.18  │
│   ▶ Read config   │  → Read(...)          │  planner: $0.07 │
│   ✓ Update value  │  ✓ Read done          │  verifier: $0.11│
│   ▸ SUBPLAN       │  ↳ SecurityAgent      │ Today:    $1.42 │
│   ▸ VERIFY (test) │                       │                │
└────────────────────────────────────────────────────────────┘
```

`Cost` panel reads `CostTracker.aggregate_by(...)` and refreshes after each step. Live USD accumulation as walk progresses.

### 8.2 New bindings

| Key | Action | Notes |
|---|---|---|
| `$` | Focus CostPanel | |
| `M` | Focus ModelPanel (NEW: shows current hint → model mapping per step) | |

### 8.3 New modals

- `CostPanel` (always-visible pane; 10% width)
- `ModelMappingModal` (on `Shift+M`, shows full policy table + active hint resolutions)
- `BudgetWarningModal` (when session cost exceeds `budget_usd_per_session`)

### 8.4 Step-level model badge

Each step in the plan tree shows a small model badge:

```
▶ Step 1/4: Read config           [sonnet]
✓ Step 2/4: Update value           [sonnet]
▸ SUBPLAN (SPECIFIER)              [sonnet]    ← from RoleDefinition.model_tier
▸ VERIFY (review)                  [sonnet]
▸ CRITIQUE                         [sonnet]
```

---

## 9. 失败模式

| Failure | Detection | Recovery |
|---|---|---|
| Unknown model in `--model` flag | `ModelConfig.__getitem__` raises KeyError | CLI errors with available model list |
| `policy.yaml` malformed YAML | `yaml.safe_load` raises `yaml.YAMLError` | `ModelPolicy.load` catches; logs warning; falls back to DEFAULT_POLICY |
| Model alias unknown | `ModelConfig.aliases` lookup fails | CLI errors with known aliases |
| LLMClient.complete() raises APIError | Bubbles up unchanged | Existing v1.1 retry/timeout logic still applies |
| CostTracker.wal.append_cost() fails (disk full, etc.) | try/except in `CostTracker.record` | Log warning; continue without persisting; in-memory buffer still works |
| Budget exceeded (advisory) | `CostTracker.aggregate_by` returns total > budget | Emit warning banner; do NOT block |
| Sub-plan model override breaks role contract | `RoleRegistry.spawn()` validates resolved model name against role's `required_capabilities` (future) | v1.2: no validation; trust user policy |
| Opus 4.8 rate-limited | APIError 429 (existing v1.1 handling) | Router catches, retries on Sonnet as fallback (configurable) |
| `nexus cost` with no cost records in WAL | `aggregate_by` returns empty list | CLI prints "No cost data yet" |
| Per-session WAL scan slow (>1k records) | Aggregation reads from cache `.nexus/cost/index.json` (rebuilt lazily) | First call after warm rebuild is slow; subsequent calls fast |
| Cost telemetry drift (token counting differs per provider) | `LLMClient._parse_*` already normalizes to `Usage(input_tokens, output_tokens)` | Router trusts `Usage`; no drift |

---

## 10. 测试策略

### 10.1 Coverage targets

| Layer | Tests | Coverage target |
|---|---|---|
| Unit — ModelPolicy.load (env, YAML, CLI precedence) | 8 tests: each precedence level, malformed YAML, missing file | 95% line coverage |
| Unit — ModelRouter.route (hint resolution, fallback) | 12 tests: each hint value, missing hint, role override, cli_override | 95% line coverage |
| Unit — CostTracker.record + aggregate_by | 10 tests: ring buffer overflow, WAL append, dimension grouping | 90% line coverage |
| Unit — ModelTier → ModelHint mapping | 4 tests: each tier | 100% |
| Unit — LLMClient unchanged | 6 regression tests (existing v1.1 suite, no modifications) | 100% |
| Integration — full walk with mixed-model hints | 6 tests: Planner=Sonnet + Security=Haiku + Review=Sonnet; verify cost records in WAL; verify final Plan state | 90% |
| Integration — `nexus cost today` end-to-end | 3 tests: empty WAL, multi-session WAL, budget-exceeded scenario | 85% |
| Backwards compat — v1.1 WAL loads in v1.2; v1.1 fixture cost-less | 2 tests: load every fixture, verify walk replay works | 100% |
| TUI — CostPanel + ModelMappingModal render | 4 tests: panel updates after step, modal opens on Shift+M | 90% |
| LLM smoke — real Anthropic API; verify different models receive different requests | 2 tests: `--model sonnet` vs `--model haiku` produces different model strings in cost records | Skipped without API key |

### 10.2 Total test target

142 (v1.1 baseline) + ~30 new = **~172 tests**.

### 10.3 v1.2 release criteria (Definition of Done)

1. All 172 tests pass; coverage ≥85% on new modules
2. `nexus cost today` returns non-empty table on any walk that issued LLM calls
3. `nexus run --model haiku` produces cost records with model=haiku, NOT sonnet
4. Backwards compat: every v1.1 WAL fixture loads + replays correctly in v1.2
5. README, ARCHITECTURE, ROADMAP updated for v1.2 (policy.yaml, cost panel, model aliases)
6. CHANGELOG entry summarizing per-phase changes
7. v1.2 git tag, GitHub release notes

---

## 11. 实施阶段

| Phase | Goal | Tasks | Est. | Dependencies |
|---|---|---|---|---|
| **A. ModelHint + ModelPolicy** | Enum + dataclass + YAML/env/CLI resolution + DEFAULT_POLICY table | 5 | 2 days | None |
| **B. ModelRouter refactor** | Refactor existing model_router.py → new hint-based API; preserve get_client() | 6 | 3 days | A |
| **C. CostTracker + WAL extension** | CostRecord, CostTracker, WAL v2 cost kind, aggregation | 5 | 3 days | A |
| **D. Call-site wiring** | Planner / Walker CRITIQUE / RoleRegistry.spawn / ReviewGate delegate / Evolver (indirect) | 8 | 4 days | B, C |
| **E. CLI + TUI** | `nexus cost` subcommand, `nexus model` list/show, `--model` flag, CostPanel, ModelMappingModal | 6 | 4 days | C, D |
| **F. Tests + release** | Unit + integration + backwards-compat + TUI + LLM smoke + README/ROADMAP/ARCHITECTURE rewrite + v1.2 tag | 5 | 3 days | All above |

**Total: 35 tasks, ~19 working days (~4 weeks at sustainable pace).**

Critical path: **A → B → C → D → E → F**. D depends on B (router) and C (cost); E depends on C (cost data) and D (call-site model names surface to CLI/TUI).

---

## 12. 风险与缓解

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Router adds latency to every LLM call | Low | Low | Router is in-memory dict lookup + single function call; ~1ms overhead |
| Cost tracking WAL bloat | Medium | Medium | Cost records are small (~200 bytes); at 1000 calls/plan × 200 bytes = 200KB; acceptable. Aggregate/index cached separately. |
| Per-role model downgrade breaks complex roles (e.g. SECURITY with Haiku misses deep analysis) | Low | Medium | SECURITY role stays on Haiku because v1.1 implementation is pattern-match only; if role evolves to need Sonnet, user upgrades via per_role YAML |
| `policy.yaml` schema drift across versions | Medium | Low | Add `policy_schema_version` field; warn on mismatch; auto-migrate known schemas |
| Opus 4.8 API not yet GA by v1.2 release | Medium | Medium | Document as "Opus 4.8 — beta"; default route still Sonnet 4.6; Opus only via explicit `--deep-plan` or per_role YAML |
| `--model` flag abuse (user picks model that can't handle task) | Low | Low | v1.2 trusts user; v1.3 may add capability validation (e.g. context_window check) |
| OpenAI/Ollama path regression — existing `src/llm/model_router.py` had OpenAI/Ollama models in registry; v1.2 drops them from defaults | Low | Low | Keep them in `ModelConfig.aliases` registry but document as opt-in; users can re-add via custom `ModelPolicy` |
| Cost drift due to model pricing changes | Medium | Medium | `ModelConfig.cost_per_1k_*` is the source of truth; users override via policy.yaml; CHANGELOG documents pricing updates |
| TUI CostPanel slow at large session counts | Low | Low | Aggregate from in-memory buffer during walk; WAL scan only at session end |
| Default Haiku 4.5 for security produces too many false positives | Low | Medium | SECURITY role's primary scan is regex/AST (no LLM); LLM delegate is fallback only; false positives affect review quality but don't block commits |

---

## 13. 未来扩展（v1.3+，超出本文档范围）

- 自动 budget cap（hard limit, kill walk if exceeded）
- 自动 plan complexity detection（用 prompt length + step count 推断 hint）
- OpenAI / Ollama provider enable（policy.yaml `provider:` field）
- Plan-level `model_hint` per-step（user 在 TUI 直接给某个 step 选 model）
- Multi-model speculation（同一 prompt fan-out 到 Sonnet + Opus，取最优）
- Cost-aware planner（planner 估算每个候选 step 的 USD，组合时预算最优）
- Per-team rate limits（policy.yaml 多用户隔离）
- Auto-evolve `policy.yaml`（Evolver 读 cost 数据，建议下调高 cost / 低 hit rate 的 hint 默认值）

---

## 14. 参考

- v1.1 design spec: `docs/superpowers/specs/2026-06-28-nexus-v11-multi-agent-memory-design.md`
- v1.1 architecture: `ARCHITECTURE.md`
- v1.1 roadmap: `ROADMAP.md`
- Existing model_router (to refactor): `src/llm/model_router.py`
- Existing client (kept): `src/llm/client.py`
- Role tier definitions: `src/agents/base.py::ModelTier`
- Role default mapping: `src/agents/default_registry.py::_ROLE_DEFAULTS`
- Verification pipeline: `src/verification/pipeline.py`
- Review gate LLM delegate: `src/verification/review_gate.py::ReviewGate._delegate_*`
- Evolver: `src/agent/evolution.py`
- WAL v2 format: `docs/superpowers/specs/2026-06-28-nexus-v11-multi-agent-memory-design.md` §4.2