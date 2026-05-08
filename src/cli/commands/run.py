"""`nexus run` — Execute a task through RalphLoop."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

# NOTE: sys.path setup is in src/cli/main.py (loaded before this module)

from ralphloop import RalphState
from ralphloop.implementation_context import ImplementationContext
from ralphloop.claude_md_loader import load_claude_md, build_llm_system_prompt
from ralphloop.agent_loop import run_agent_loop, TOOL_DEFINITIONS
from ralphloop.tdd_enforcer import TDDEnforcer
from llm.client import LLMClient, Provider
from llm.model_router import ModelRouter
from self_evolution import SelfEvolutionEngine


def _detect_provider() -> tuple[Provider, str, str, str]:
    """Auto-detect best available LLM provider. Returns (provider, api_key, base_url, model)."""
    import json as _json

    # Check Claude settings (CC Switch)
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_env: dict[str, str] = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings_env = _json.load(f).get("env", {})
        except Exception:
            pass

    # ENV takes precedence
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or settings_env.get("ANTHROPIC_AUTH_TOKEN", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or settings_env.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or settings_env.get("ANTHROPIC_BASE_URL", "")
    model = os.environ.get("ANTHROPIC_MODEL") or settings_env.get("ANTHROPIC_MODEL", "")

    # Set env for subprocess access
    if auth_token:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if base_url:
        os.environ["ANTHROPIC_BASE_URL"] = base_url
    if model:
        os.environ["ANTHROPIC_MODEL"] = model

    if auth_token:
        return Provider.ANTHROPIC, auth_token, base_url, model
    if api_key:
        return Provider.ANTHROPIC, api_key, base_url, model
    if os.environ.get("OPENAI_API_KEY"):
        return Provider.OPENAI, os.environ["OPENAI_API_KEY"], "", ""
    # Ollama (local)
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        if req.status == 200:
            return Provider.OLLAMA, "local", "", ""
    except Exception:
        pass
    return Provider.ANTHROPIC, "", base_url, ""


def _get_model_for_provider(provider: Provider, settings_model: str = "") -> str:
    """Get best model for provider. Prefers: env > settings.json > provider default."""
    if os.environ.get("ANTHROPIC_MODEL"):
        return os.environ["ANTHROPIC_MODEL"]
    if settings_model:
        return settings_model
    if provider == Provider.ANTHROPIC:
        return "claude-sonnet-4-20250514"
    if provider == Provider.OPENAI:
        return os.environ.get("OPENAI_MODEL", "gpt-4o")
    if provider == Provider.OLLAMA:
        return os.environ.get("OLLAMA_MODEL", "llama3")
    return "claude-sonnet-4-20250514"


class RalphLoopExecutor:
    """Bridges RalphLoop orchestrator with run_agent_loop.

    This version composes the FULL 6-LAYER RalphLoopExecutor from src/ralphloop/executor.py
    with the CLI presentation layer (click.echo, approval, security scan, etc.).

    Layers:
        1. WALManager         — crash recovery journaling
        2. CheckpointManager  — full state snapshots
        3. SelfEvolutionEngine — cross-session error learning
        4. ModelRouter        — smart model selection per task
        5. SubagentIntegration — parallel Implementer + Reviewer
        6. TDDEnforcer        — RED→GREEN→REFACTOR discipline

    vs the old simplified executor which had NONE of these wired.
    """

    def __init__(
        self,
        project_path: str,
        tdd_enabled: bool = True,
        system_prompt: str | None = None,
        streaming: bool = False,
    ) -> None:
        self.project_path = Path(project_path)
        self.tdd_enabled = tdd_enabled
        self.streaming = streaming

        # ── Detect LLM provider (CLI-level, not executor-level) ────────────
        self.provider, self.api_key, detected_base_url, settings_model = _detect_provider()
        self.model = _get_model_for_provider(self.provider, settings_model)

        # ── Build full 6-layer executor ────────────────────────────────────
        # Import here to avoid circular imports at module load time
        from ralphloop.executor import RalphLoopExecutor as SixLayerExecutor
        from ralphloop.agent_loop import AgentLoopConfig
        from ralphloop.claude_md_loader import load_claude_md, build_llm_system_prompt
        from context.wal import WALManager
        from context.checkpoint import CheckpointManager
        from self_evolution import SelfEvolutionEngine
        from llm.model_router import ModelRouter, TaskType
        from llm.client import LLMClient

        # WAL
        wal_dir = Path.home() / ".nexus" / "wal"
        wal_dir.mkdir(parents=True, exist_ok=True)
        self._wal = WALManager(wal_dir=wal_dir)

        # Checkpoint
        ckpt_db = Path.home() / ".nexus" / "checkpoints.db"
        ckpt_db.parent.mkdir(parents=True, exist_ok=True)
        self._ckpt = CheckpointManager(db_path=ckpt_db)

        # Self-Evolution
        skills_dir = Path.home() / ".hermes" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        error_log = Path.home() / ".nexus" / "error_log.jsonl"
        self._evo = SelfEvolutionEngine(skills_dir=skills_dir, error_log_path=error_log)
        self._evo.load_existing_skills()

        # Model Router
        base_url = detected_base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self._router = ModelRouter(
            api_keys={self.provider: self.api_key},
            base_urls={self.provider: base_url} if base_url else {},
            preferred_provider=self.provider,
        )
        default_model = self._router.select_model(TaskType.CODE, requires_tools=True)
        self._llm = self._router.get_client(default_model)

        # Agent loop config
        self.config = AgentLoopConfig(
            max_turns=200,
            tool_timeout=30,
            context_window=200_000,
            stop_on_content=False,
            streaming=streaming,
        )

        # System prompt from CLAUDE.md
        if system_prompt:
            self.system_prompt = system_prompt
        else:
            claude_md = load_claude_md(str(self.project_path))
            self.system_prompt = build_llm_system_prompt(str(self.project_path)) if claude_md else ""

        # TDD enforcer
        from ralphloop.tdd_enforcer import TDDEnforcer
        self._tdd = TDDEnforcer() if tdd_enabled else None

        # RalphLoop orchestrator state (CLI-level tracking)
        self.state = RalphState.PLAN
        self.retries = 0
        self.MAX_RETRIES = 3
        self.turns = 0

        # ── Six-layer executor for internal orchestration ────────────────────
        self._executor = SixLayerExecutor(
            workdir=self.project_path,
            llm_provider=self.provider,
            llm_api_key=self.api_key,
            llm_base_url=base_url,
            enable_wal=True,
            enable_checkpoint=True,
            enable_self_evolution=True,
            enable_model_router=True,
            enable_parallel_subagents=True,
            enable_tdd=tdd_enabled,
            checkpoint_interval=5,
            max_retries=3,
            model_router=self._router,
            custom_tools=TOOL_DEFINITIONS,
        )

        # Metrics accumulators
        self._skills_learned_count = 0
        self._checkpoints_saved_count = 0
        self._wal_entries_count = 0

        # Pre-load existing skills
        self._evo.load_existing_skills()

    def execute_task(self, task: str) -> dict[str, Any]:
        """Execute a task through RalphLoop states.

        Uses the full 6-layer executor with parallel subagents for ACT,
        plus CLI presentation layer (approval, security scan, pytest).
        """
        self.retries = 0
        self.turns = 0

        click.echo(f"\n{'='*60}")
        click.echo(f"Nexus RalphLoop | Task: {task[:60]}")
        click.echo(f"Provider: {self.provider.value} | Model: {self.model}")
        click.echo(f"Project: {self.project_path}")
        click.echo(f"TDD: {'ON' if self.tdd_enabled else 'OFF'}")
        click.echo(f"Streaming: {'ON' if self.streaming else 'OFF'}")
        click.echo(f"WAL: ON | Checkpoint: ON | SelfEvo: ON | Parallel: ON")
        click.echo(f"{'='*60}\n")

        # Streaming callback
        def _streaming_cb(token: str) -> None:
            click.echo(token, nl=False)

        # ── Use the six-layer executor's run_task ─────────────────────────
        # This runs PLAN → ACT → VERIFY → REFLECT with all 6 layers active
        result = self._executor.run_task(
            task=task,
            spec_md=None,
            constraints=[],
            max_turns_per_state=20,
        )

        self.turns = result.metrics.total_iterations * 5  # rough estimate
        self._skills_learned_count = result.skills_learned
        self._checkpoints_saved_count = result.checkpoints_saved
        self._wal_entries_count = result.wal_entries

        # ── CLI: approval + ACT gates (leverages run.py's proven UX) ──────
        # Get changed files from executor's context
        ctx = self._executor._current_context
        changed_files = ctx.get_changed_files() if ctx else []

        if changed_files:
            click.echo(f"\n[APPROVAL] {len(changed_files)} file(s) changed:")
            for f in changed_files[:10]:
                click.echo(f"  - {f}")
            if len(changed_files) > 10:
                click.echo(f"  ... and {len(changed_files) - 10} more")

            approval = click.confirm("\nProceed to final verification?")
            if not approval:
                click.echo("[ABORT] User rejected changes")
                return {
                    "success": False,
                    "turns": self.turns,
                    "final_state": "ABORT",
                    "content": "User rejected changes during approval",
                    "tool_count": len(ctx.tool_results) if ctx else 0,
                }

        # ── ACT 后验证 (same as original run.py, runs on top of 6-layer) ─
        if changed_files:
            self._run_act_gates(ctx, changed_files)

        click.echo(f"\n{'='*60}")
        click.echo(f"Final State: {result.final_state.name}")
        click.echo(f"Success: {result.success}")
        click.echo(f"Skills Learned: {result.skills_learned}")
        click.echo(f"Checkpoints: {result.checkpoints_saved}")
        click.echo(f"WAL Entries: {result.wal_entries}")
        click.echo(f"Cost: ${result.total_cost_usd:.4f}")
        click.echo(f"{'='*60}")

        return {
            "success": result.success,
            "turns": self.turns,
            "final_state": result.final_state.name,
            "content": result.summary,
            "tool_count": len(ctx.tool_results) if ctx else 0,
            "skills_learned": result.skills_learned,
            "checkpoints_saved": result.checkpoints_saved,
            "wal_entries": result.wal_entries,
            "cost_usd": result.total_cost_usd,
        }

    def _run_act_gates(self, ctx, changed_files: list[str]) -> None:
        """Run ACT-phase verification gates (security + pytest + mypy)."""
        click.echo(f"\n[VERIFY] Running ACT gates on {len(changed_files)} changed files...")

        # Security scan
        codes = {}
        for f in changed_files:
            if f.endswith((".py", ".js", ".ts", ".jsx", ".tsx")):
                content = ctx.get_file_content(f)
                if content:
                    codes[f] = content

        if codes:
            from src.verification.security_scan import SecurityScan
            scanner = SecurityScan()
            blocked = False
            blocking_issues = []
            for f, code in codes.items():
                scan_result = scanner.scan(code, file_path=f)
                if not scan_result.passed:
                    blocked = True
                    for finding in scan_result.findings:
                        blocking_issues.append(
                            f"[SECURITY] {f}:{finding.line_number} - {finding.title}"
                        )
            if blocked:
                click.echo(f"\n[!!] Security scan FAILED:")
                for issue in blocking_issues:
                    click.echo(f"  - {issue}")
            else:
                click.echo("[OK] Security scan passed")

        # Pytest on test files
        test_files = [f for f in changed_files if f.startswith("tests/") or f.endswith("_test.py") or f.endswith("_tests.py")]
        if test_files:
            click.echo(f"\n[VERIFY] Running pytest on {len(test_files)} test files...")
            import subprocess
            test_result = subprocess.run(
                ["python", "-m", "pytest", "-x", "-q", "--tb=short"] + test_files,
                capture_output=True,
                text=True,
                cwd=str(self.project_path),
            )
            if test_result.returncode != 0:
                click.echo(f"[!!] Pytest FAILED:\n{test_result.stdout}\n{test_result.stderr}")
            else:
                click.echo("[OK] Pytest passed")

        # MyPy on Python files (non-blocking)
        py_files = [f for f in changed_files if f.endswith(".py") and not f.startswith("tests/")]
        if py_files:
            click.echo(f"\n[VERIFY] Running mypy type check on {len(py_files)} files...")
            import subprocess
            mypy_result = subprocess.run(
                ["python", "-m", "mypy", "--ignore-missing-imports", "--no-error-summary"] + py_files,
                capture_output=True,
                text=True,
                cwd=str(self.project_path),
            )
            if "error:" in mypy_result.stdout:
                click.echo(f"[!!] MyPy issues found:\n{mypy_result.stdout}")

    def _has_errors(self) -> bool:
        ctx = self._executor._current_context
        if not ctx:
            return False
        for tr in ctx.tool_results:
            content = str(tr.get("result", ""))
            if "ERROR:" in content or "error:" in content:
                return True
        return False

    def _format_tool_results(self) -> str:
        ctx = self._executor._current_context
        if not ctx:
            return "(no context)"
        lines = []
        for tr in ctx.tool_results[-10:]:
            name = tr.get("tool", "?")
            result = str(tr.get("result", ""))[:200]
            lines.append(f"[{name}] {result}")
        return "\n".join(lines) if lines else "(no tool results yet)"

    def _handle_failure(self, phase: str, result: Any) -> dict[str, Any]:
        click.echo(f"\n[FAIL] {phase} failed: {str(result)[:200] if result else 'no result'}")
        return {
            "success": False,
            "turns": self.turns,
            "final_state": "ABORT",
            "content": str(result) if result else "",
            "tool_count": 0,
            "error": f"{phase} phase failed",
        }


@click.command()
@click.option("--task", "-t", required=True, help="Task description")
@click.option("--workdir", "-C", type=click.Path(file_okay=False), help="Working directory")
@click.option("--tdd/--no-tdd", default=True, help="Enable TDD enforcement")
@click.option("--stream/--no-stream", default=False, help="Enable streaming token output")
def run(task: str, workdir: str | None, tdd: bool, stream: bool) -> int:
    """Run a task through RalphLoop."""
    project_path = Path(workdir or os.getcwd()).expanduser().resolve()

    # Load CLAUDE.md system prompt
    system_prompt = ""
    try:
        claude_md = load_claude_md(str(project_path))
        if claude_md:
            system_prompt = build_llm_system_prompt(str(project_path))
    except Exception as e:
        click.echo(f"[WARN] Could not load CLAUDE.md: {e}", err=True)

    # Execute
    executor = RalphLoopExecutor(
        project_path=str(project_path),
        tdd_enabled=tdd,
        system_prompt=system_prompt,
        streaming=stream,
    )

    result = executor.execute_task(task)

    click.echo(f"\nResult: {json.dumps(result, indent=2, default=str)}")
    return 0 if result.get("success") else 1
