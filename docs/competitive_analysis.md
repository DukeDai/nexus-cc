# Nexus vs Claude Code — Competitive Win Conditions

> **Claude Code is the baseline. Nexus must surpass it in real tasks, not just feature counts.**

## Win Conditions

| Capability | Claude Code | Nexus | Nexus Wins When |
|-----------|-------------|-------|-----------------|
| **Self-Evolution** | ❌ No memory | ✅ Skill system | Error happens once, never again |
| **WAL Crash Recovery** | ❌ Loses all progress | ✅ WAL + Checkpoint | Interrupt/kill → resume from last checkpoint |
| **TDD Enforcement** | ❌ No TDD | ✅ RED→GREEN→REFACTOR gate | >80% of tasks use TDD cycle |
| **Model Cost** | ❌ Uses same model always | ✅ ModelRouter | Same quality, 3-5x lower cost |
| **Parallel Agents** | ❌ Single agent | ✅ SubagentIntegration | Code + review simultaneously |
| **Context Efficiency** | ❌ Truncates | ✅ 4-tier budget | 2x more task steps per $ |
| **Working Buffer** | ❌ No sandbox | ✅ Isolated buffers | Experiment without risk |

## What Claude Code Does Better

| Gap | Impact | Mitigation |
|-----|--------|------------|
| Streaming UX (real-time tokens) | Medium | Nexus has streaming_callback, needs TUI integration |
| Tool breadth (50+ tools) | Medium | Add desktop-control, CDP, GUI tools |
| Git workflow (interactive) | Low | Implement git add -p, interactive rebase |
| Project context (CLAUDE.md deep) | Medium | Already implemented, needs real-world testing |
| Error message quality | High | Nexus must match / exceed |

## 5 Tasks to Prove Supremacy

1. **"Implement a REST API with FastAPI + tests"** → Nexus TDD vs Claude Code ad-hoc
2. **"Fix this bug: ModuleNotFoundError after renaming imports"** → Nexus self-evolution vs repeated failure
3. **"Add authentication to this Flask app"** → Nexus parallel agents vs sequential
4. **"Crash the agent mid-task, then resume"** → WAL recovery vs从头开始
5. **"Implement a feature using only $0.10 of API credits"** → ModelRouter vs Claude Code flat-rate

## Success Metrics

- Self-Evolution: ≥5 cross-session errors learned
- WAL: ≥3 crash/resume cycles successful
- TDD: ≥80% of implementation tasks follow RED→GREEN→REFACTOR
- ModelRouter: ≥50% cost reduction vs Anthropic flat-rate
- Parallel: Implementer + Reviewer speedup ≥1.5x vs sequential
