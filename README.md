# Nexus — Next-Generation Autonomous Coding Agent

A self-improving coding agent that surpasses claude-code in reliability, autonomy, and code quality.

## Key Features

### RalphLoop Orchestration
Closed-loop self-correction with explicit state machine: **Plan → Act → Verify → Reflect**

### Multi-Agent Specialization
- **Specifier**: Requirements → Specification
- **Implementer**: Spec → Code (TDD enforced)
- **Reviewer**: Quality gate
- **Security**: Automated security scanning

### Self-Improvement
- Captures mistakes automatically
- Authors prevention skills
- Loads relevant skills before each task

### Verification Gates
- **TDD Gate**: Test-before-code enforcement
- **Security Scan**: Fail-closed on any finding
- **Test Gate**: Baseline comparison (NEW failures = block)
- **Review Gate**: Independent reviewer subagent

## Installation

```bash
pip install -e .
```

## Usage

```bash
# Run a task
nexus run "Add user authentication to the API"

# Review a file
nexus review src/auth.py

# Check status
nexus status

# List learned skills
nexus skills --list
nexus skills --patterns
```

## Architecture

```
Nexus
├── RalphLoop          # State machine orchestration
├── Agents             # Specialized agents
│   ├── Specifier     # Requirements → Spec
│   ├── Implementer   # TDD: Test-first coding
│   ├── Reviewer     # Quality verification
│   └── Security     # Security scanning
├── Verification      # Pre-commit pipeline
│   ├── TDD Gate     # Test-first enforcement
│   ├── Security Scan
│   ├── Test Gate    # Baseline comparison
│   └── Review Gate  # Independent review
├── Skills            # Self-improvement
│   ├── Capture      # Mistake capture
│   ├── Author       # Skill authoring
│   └── Loader       # Skill loading
└── Context           # Budget monitoring
    ├── Monitor      # 4-tier degradation
    └── Checkpoint   # State recovery
```

## Success Metrics

| Metric | Claude Code | Nexus Target |
|--------|-------------|--------------|
| Task completion rate | ~85% | >95% |
| Silent failures | Occasional | Zero |
| Test coverage | Optional | Mandatory |
| Security issues | Low | Zero |
| Self-improvement | Per-project | Cross-session |

## License

MIT
