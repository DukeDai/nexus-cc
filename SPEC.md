# Nexus — Next-Generation Autonomous Coding Agent

## Vision Statement

Nexus is an autonomous coding agent that surpasses claude-code in reliability, autonomy, and code quality. It combines the best of claude-code's thoughtful architecture, Codex's speed, and opencode's openness, while introducing novel orchestration patterns (RalphLoop), genuine multi-agent specialization, and continuous self-improvement. The ultimate measure of success: when a senior engineer can trust Nexus to implement a complex feature end-to-end — spec → code → tests → review → commit — without intervention.

---

## 1. Competitive Analysis

### 1.1 Claude Code (Anthropic)

| Dimension | Strengths | Weaknesses |
|-----------|-----------|------------|
| **Architecture** | Subagent system, hooks, CLAUDE.md memory, MCP integration | No true parallel execution of independent tasks |
| **Context** | Strong context management, `/compact` to reduce | Still leaks context; no formal budget discipline |
| **Verification** | `/review`, `/security-review` commands | No automatic verification gate before commit |
| **TDD** | Supports but doesn't enforce | No mandatory test-first discipline |
| **Self-correction** | Can rewind, resume sessions | No systematic error recovery protocol |
| **Multi-model** | Sonnet/Opus/Haiku switching mid-session | No task-model routing based on complexity |
| **Learning** | Auto-memory per project | No cross-session pattern learning |
| **Reliability** | Solid, predictable | Loop runaway possible without `--max-turns` |

### 1.2 Codex (OpenAI)

| Dimension | Strengths | Weaknesses |
|-----------|-----------|------------|
| **Speed** | Very fast execution | Quality inconsistent |
| **Git integration** | Deep worktree support | No review workflow |
| **Modes** | `--full-auto`, `--yolo` flexibility | Yolo mode too dangerous; full-auto approval confusion |
| **Multi-agent** | Built-in agent teams | Unstructured, no specialization |
| **Context** | Good with large diffs | No structured context budgeting |
| **Verification** | PR review command | No automated test enforcement |

### 1.3 OpenCode

| Dimension | Strengths | Weaknesses |
|-----------|-----------|------------|
| **Provider agnostic** | Works with any LLM | No model intelligence optimization |
| **Open source** | Transparency | Less polish than proprietary |
| **Session management** | Good session list/stats | No structured learning across sessions |
| **CLI ergonomics** | Clean `run` vs interactive modes | No TDD enforcement |
| **Review** | Built-in PR review | No security scanning automation |

---

## 2. Ultimate Goal & Key Differentiators

**Ultimate Goal:** A coding agent that a senior engineer can trust to implement complex features autonomously — from understanding requirements to verified, committed code — with quality that meets or exceeds what the senior engineer would produce.

### Key Differentiators (vs Claude Code)

| # | Differentiator | Why It Matters |
|---|----------------|----------------|
| D1 | **RalphLoop Orchestration** | Closed-loop self-correction with explicit state machine transitions (Plan→Act→Verify→Reflect). No silent failures. |
| D2 | **Task-Model Routing** | Trivial tasks use Haiku-equivalent; complex reasoning uses Sonnet/Opus. Cost-efficiency without sacrificing quality. |
| D3 | **Mandatory TDD Gate** | Every implementation task must produce tests BEFORE code. Red-Green-Refactor enforced by orchestration. |
| D4 | **Multi-Agent Specialization** | Separate agents for: Planner, Implementer, Reviewer, Security Auditor, Performance Analyzer. Each expert in their domain. |
| D5 | **Cross-Session Pattern Memory** | Learned patterns stored in durable skill format. Mistakes from session A prevent failures in session B. |
| D6 | **Formal Context Budget Discipline** | Four-tier degradation model (PEAK/GOOD/DEGRADING/POOR) with explicit gate actions at each tier. |
| D7 | **Structured Self-Improvement** | After each task: capture what worked, what failed, update skill library. The agent gets genuinely better over time. |
| D8 | **Verification Before Commit** | No code is committed without passing: security scan + lint + tests + review. Every commit is verified. |
| D9 | **Escalation Protocol** | When RalphLoop exhausts retries, explicit escalation to human with concrete options — never silent failure or infinite loop. |
| D10 | **Deterministic Output** | Same task with same context produces consistent, reproducible results. No randomness in quality. |

---

## 3. Architecture

### 3.1 RalphLoop — The Core Orchestration Engine

```
┌─────────────────────────────────────────────────────────────────────┐
│                         NEXUS RalphLoop                             │
│                                                                     │
│   ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌──────────┐        │
│   │  PLAN   │───▶│   ACT    │───▶│ VERIFY  │───▶│ REFLECT  │        │
│   │  state  │    │  state   │    │  state  │    │  state   │        │
│   └────┬────┘    └────┬─────┘    └────┬────┘    └────┬─────┘        │
│        │              │               │              │              │
│        │   ┌──────────┘               │              │              │
│        │   │     error/verify fail   │              │              │
│        │   ▼                          ▼              │              │
│        │   ───────────────────────────────           │              │
│        │         retry up to 3x         ────────────┤              │
│        │   ───────────────────────────────           │              │
│        │                                       ┌─────┴─────┐        │
│        └───────────────────────────────────────▶│  COMMIT   │        │
│                                                  │  (done)   │        │
│                                                  └───────────┘        │
└─────────────────────────────────────────────────────────────────────┘
```

**State Machine Rules:**

| Transition | Trigger | Action |
|------------|---------|--------|
| PLAN → ACT | Valid spec produced | Dispatch implementer subagent |
| ACT → VERIFY | Implementation complete | Run TDD gate + reviewer subagent |
| VERIFY → REFLECT | Verification passed | Analyze patterns, capture learnings |
| VERIFY → PLAN | Verification failed (≤3 retries) | Revise spec based on error feedback |
| VERIFY → ESCALATE | 3 consecutive verify failures | Human decision required |
| REFLECT → PLAN | Next task in queue | Continue to next task |
| REFLECT → COMMIT | All tasks done | Final review, commit |
| Any → ABORT | Context budget POOR tier | Checkpoint, stop, report |

### 3.2 Multi-Agent Specialization

```
                    ┌──────────────────────┐
                    │   Nexus Orchestrator  │
                    │   (RalphLoop State    │
                    │    Machine Control)   │
                    └──────────┬───────────┘
                               │ dispatches specialized agents
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐
  │   SPECIFIER   │    │  IMPLEMENTER  │    │   REVIEWER    │
  │ - Understand  │    │ - TDD enforce │    │ - Spec check  │
  │   requirements│    │ - Write code  │    │ - Quality gate│
  │ - Write spec  │    │ - Run tests   │    │ - Logic errors│
  └───────────────┘    └───────────────┘    └───────────────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │  SECURITY AUDITOR     │
                    │  - Scan for secrets  │
                    │  - SQL/XSS injection  │
                    │  - Path traversal    │
                    └──────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │  PERFORMANCE ANALYZER│
                    │  - Algorithmic compex│
                    │  - Query optimization│
                    └──────────────────────┘
```

**Agent Communication Protocol:**
- Each agent receives a structured context block (never unbounded)
- Agents return typed responses with confidence scores
- No agent reviews its own work (independence enforced)
- All inter-agent communication goes through orchestrator

### 3.3 Skill Memory System

```
┌─────────────────────────────────────────────────────────────┐
│                    Nexus Skill Library                        │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Language    │  │  Framework   │  │   Domain     │     │
│  │  Skills      │  │  Skills      │  │  Skills      │     │
│  │  ──────────  │  │  ──────────  │  │  ──────────  │     │
│  │  • Python    │  │  • FastAPI    │  │  • Auth      │     │
│  │  • TypeScript│  │  • React      │  │  • Payments  │     │
│  │  • Rust      │  │  • PostgreSQL │  │  • ML Pipelines│   │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Pattern     │  │   Mistake    │  │   Project     │     │
│  │  Skills      │  │   Capture    │  │   Context     │     │
│  │  ──────────  │  │  ──────────  │  │  ──────────  │     │
│  │  • Auth flow │  │  • What went │  │  • CLAUDE.md │     │
│  │  • Error hndl│  │    wrong     │  │  • Commands  │     │
│  │  • API design│  │  • How to fix│  │  • Standards │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

**Self-Improvement Protocol:**
1. After each task, Reviewer identifies what patterns emerged
2. Reflect agent extracts mistakes → writes to Mistake Capture
3. Implementer agents query relevant skills before starting
4. New patterns auto-authored as skills via skill_manage

---

## 4. Verification System

### 4.1 TDD Enforcement Gate

```
Task Received
     │
     ▼
┌─────────────────────────────────────┐
│ 1. Write Failing Test (RED)        │
│    - Test must fail before code    │
│    - Test quality gate: must test   │
│      behavior, not implementation  │
└──────────────┬──────────────────────┘
               ▼
┌─────────────────────────────────────┐
│ 2. Run Test → MUST FAIL            │
│    - Fail = test is valid          │
│    - Pass = test is tautological   │
│      (auto-reject, rewrite)        │
└──────────────┬──────────────────────┘
               ▼
┌─────────────────────────────────────┐
│ 3. Write Minimal Implementation    │
│    (GREEN)                          │
└──────────────┬──────────────────────┘
               ▼
┌─────────────────────────────────────┐
│ 4. Run Tests → MUST PASS           │
└──────────────┬──────────────────────┘
               ▼
┌─────────────────────────────────────┐
│ 5. Refactor (REFACTOR)              │
│    - Maintain all tests passing    │
│    - No new functionality           │
└──────────────┬──────────────────────┘
               ▼
         VERIFY PASS
```

### 4.2 Pre-Commit Verification Pipeline

Before any `git commit`:

1. **Security Scan** (auto-FAIL on any finding)
   - Hardcoded secrets (API keys, passwords, tokens)
   - Shell injection (`os.system`, `shell=True`)
   - SQL injection (string formatting in queries)
   - Path traversal
   - Dangerous deserialization (`pickle.loads`, `eval`)

2. **Test Gate** (baseline comparison)
   - Run full test suite
   - Compare against baseline failure count
   - NEW failures = regression = block commit

3. **Lint Gate** (non-blocking but reported)
   - Auto-fix if possible
   - Manual review if not

4. **Independent Review** (fresh subagent)
   - Passes ONLY if: no security concerns AND no logic errors
   - Suggestions are non-blocking

---

## 5. Context Budget Discipline

### 5.1 Four-Tier Monitoring

| Tier | Usage | Orchestrator Action |
|------|-------|---------------------|
| **PEAK** (0-30%) | Full operations | Spawn parallel agents, read freely |
| **GOOD** (30-50%) | Normal operations | Prefer frontmatter reads |
| **DEGRADING** (50-70%) | Economize | Frontmatter-only, minimal inlining, warn user |
| **POOR** (70%+) | Emergency | Checkpoint immediately, complete current task, stop |

### 5.2 Context Budget Actions

- At **DEGRADING**: Send user warning, begin limiting subagent outputs
- At **POOR**: Trigger [Abort gate](gates-taxonomy) — checkpoint progress, stop all operations
- Subagent outputs: Read frontmatter only (verdict/summary), not full bodies
- Large artifacts: Store to disk, subagent reads from disk

---

## 6. Escalation Protocol

When the RalphLoop exhausts retries (3x on same task):

```
┌─────────────────────────────────────────────────────────┐
│                 ESCALATION GATE                          │
│                                                         │
│  Situation: 3 consecutive failures on task: [task]    │
│                                                         │
│  Options presented to human:                           │
│                                                         │
│  1. FORCE-MERGE: Accept current state, manual review   │
│  2. REWRITE: Discard and re-spec from scratch          │
│  3. ABANDON: Skip task, continue with remaining tasks  │
│  4. DECOMPOSE: Break task into smaller subtasks        │
│                                                         │
│  Human selects → workflow resumes on chosen path       │
└─────────────────────────────────────────────────────────┘
```

**Never:** Loop forever, guess silently, or produce silently wrong code.

---

## 7. Skill Self-Authoring

After each completed task, Nexus automatically:

1. **Captures Mistake Patterns**
   - What went wrong during implementation?
   - What error patterns appeared in tests?
   - What edge cases were missed?

2. **Authors Recovery Skills**
   - When a bug pattern is identified, a skill is written
   - Format: trigger condition → diagnosis → fix
   - Next occurrence: agent loads skill, applies fix directly

3. **Updates Project Context**
   - Updates CLAUDE.md-equivalent with new learnings
   - Records command aliases, project conventions
   - Captures architectural decisions

---

## 8. Implementation Plan

### Phase 1: Core Infrastructure
- RalphLoop state machine implementation
- Basic orchestration (Plan→Act→Verify→Reflect cycle)
- Single-agent mode (no parallelism yet)

### Phase 2: Multi-Agent Specialization
- Implementer, Reviewer, Security Auditor agents
- Agent communication protocol
- Independent verification (no self-review)

### Phase 3: TDD Enforcement
- Red-Green-Refactor gate
- Test-before-code discipline
- Test quality validation

### Phase 4: Self-Improvement
- Mistake capture system
- Skill auto-authoring
- Cross-session pattern memory

### Phase 5: Context Budget Discipline
- Four-tier monitoring
- Proactive warning system
- Abort gate implementation

### Phase 6: Testing & Comparison
- Benchmark against claude-code on identical tasks
- Measure: reliability, quality, cost, speed
- Gap analysis and iteration

---

## 9. Success Metrics

| Metric | Claude Code Baseline | Nexus Target |
|--------|---------------------|--------------|
| Task completion rate | ~85% | >95% |
| Silent failures | Occasional | Zero (explicit state transitions) |
| Test coverage enforcement | Optional | Mandatory per task |
| Security issues in output | Low | Zero (scan gate) |
| Context budget awareness | Implicit | Explicit 4-tier model |
| Self-improvement | Per-project memory | Cross-session pattern learning |
| TDD discipline | Suggested | Enforced |
| Escalation clarity | N/A | Explicit options to human |
| Commit verification | Optional | Mandatory pipeline |

---

## 10. File Structure

```
nexus/
├── src/
│   ├── ralphloop/           # RalphLoop state machine
│   │   ├── __init__.py
│   │   ├── states.py        # State definitions
│   │   ├── transitions.py   # State transition logic
│   │   └── orchestrator.py # Main orchestration engine
│   ├── agents/              # Specialized agents
│   │   ├── __init__.py
│   │   ├── specifier.py     # Requirements → spec
│   │   ├── implementer.py   # Spec → code + tests
│   │   ├── reviewer.py      # Quality gate
│   │   ├── security.py      # Security scan
│   │   └── performance.py   # Performance analysis
│   ├── skills/              # Skill memory system
│   │   ├── __init__.py
│   │   ├── capture.py       # Mistake capture
│   │   ├── author.py        # Skill authoring
│   │   └── loader.py        # Skill loading
│   ├── verification/        # Verification pipelines
│   │   ├── __init__.py
│   │   ├── tdd_gate.py      # TDD enforcement
│   │   ├── security_scan.py # Security scanning
│   │   ├── test_gate.py     # Test execution
│   │   └── review_gate.py   # Independent review
│   └── context/             # Context budget management
│       ├── __init__.py
│       ├── monitor.py       # Budget tier monitoring
│       └── checkpoint.py    # State checkpointing
├── tests/
│   ├── ralphloop/
│   ├── agents/
│   └── integration/
├── nexus.py                 # Main CLI entry point
├── pyproject.toml
└── README.md
```
