"""`nexus run` — Execute a task through RalphLoop."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

# NOTE: sys.path setup is in src/cli/main.py (loaded before this module)

from ralphloop import RalphState
from ralphloop.claude_md_loader import load_claude_md, build_llm_system_prompt
from ralphloop.agent_loop import TOOL_DEFINITIONS  # passed to executor
from llm.client import Provider  # used by _detect_provider
from self_evolution import SelfEvolutionEngine  # used for skills loading


def _detect_provider() -> tuple[Provider, str, str, str]:
    """Auto-detect best available LLM provider. Returns (provider, api_key, base_url, model).

    Priority: SCNET_API_KEY env > ANTHROPIC_API_KEY env > settings > Ollama > default
    """
    import json as _json

    # ENV: SCNET (sk-sp- prefix) — highest priority, checked first
    scnet_key = os.environ.get("SCNET_API_KEY", "")
    if scnet_key and scnet_key.startswith("sk-sp-"):
        os.environ["SCNET_API_KEY"] = scnet_key
        scnet_base = os.environ.get("SCNET_BASE_URL", "https://api.scnet.cn/api/llm/anthropic/v1")
        scnet_model = os.environ.get("SCNET_MODEL", "MiniMax-M2.5")
        return Provider.MINIMAX_CN, scnet_key, scnet_base, scnet_model

    # Check Claude settings (CC Switch)
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_env: dict[str, str] = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings_env = _json.load(f).get("env", {})
        except Exception:
            pass

    # ENV or settings for Anthropic
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
    if provider == Provider.MINIMAX_CN:
        return os.environ.get("SCNET_MODEL", "MiniMax-M2.5")
    return "claude-sonnet-4-20250514"


class RalphLoopExecutor:
    """Thin CLI wrapper over src/ralphloop.executor.RalphLoopExecutor.

    Only adds CLI-specific presentation (approval, verification gates, output).
    All 6-layer logic (WAL/Checkpoint/SelfEvo/ModelRouter/Subagents/TDD)
    is delegated to the real executor — no duplication.

    vs the old run.py which re-implemented everything locally and got out of sync.
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

        # Detect LLM provider (CLI-level auth only)
        self.provider, self.api_key, detected_base_url, settings_model = _detect_provider()
        self.model = _get_model_for_provider(self.provider, settings_model)
        base_url = detected_base_url or os.environ.get("ANTHROPIC_BASE_URL", "")

        # System prompt from CLAUDE.md
        if system_prompt:
            self.system_prompt = system_prompt
        else:
            claude_md = load_claude_md(str(self.project_path))
            self.system_prompt = build_llm_system_prompt(str(self.project_path)) if claude_md else ""

        # Pre-load Self-Evolution skills (needed for CLI display metrics)
        skills_dir = Path.home() / ".hermes" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        self._evo = SelfEvolutionEngine(skills_dir=skills_dir, error_log_path=Path.home() / ".nexus" / "error_log.jsonl")
        self._evo.load_existing_skills()

        # Thin wrapper: real 6-layer executor
        from ralphloop.executor import RalphLoopExecutor as SixLayerExecutor
        from llm.model_router import ModelRouter, TaskType

        # Build ModelRouter (same as CLI would use)
        self._router = ModelRouter(
            api_keys={self.provider: self.api_key},
            base_urls={self.provider: base_url} if base_url else {},
            preferred_provider=self.provider,
        )

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

        # State tracking
        self.state = RalphState.PLAN
        self.turns = 0

    def execute_task(self, task: str) -> dict[str, Any]:
        """Execute via 6-layer executor, then run CLI verification gates."""
        self.turns = 0

        click.echo(f"\n{'='*60}")
        click.echo(f"Nexus RalphLoop | Task: {task[:60]}")
        click.echo(f"Provider: {self.provider.value} | Model: {self.model}")
        click.echo(f"Project: {self.project_path}")
        click.echo(f"TDD: {'ON' if self.tdd_enabled else 'OFF'} | Streaming: {'ON' if self.streaming else 'OFF'}")
        click.echo(f"WAL: ON | Checkpoint: ON | SelfEvo: ON | Parallel: ON")
        click.echo(f"{'='*60}\n")

        # ── 6-layer execution ─────────────────────────────────────────────
        result = self._executor.run_task(
            task=task,
            spec_md=None,
            constraints=[],
            max_turns_per_state=20,
        )

        self.turns = result.metrics.total_iterations * 5

        # ── CLI: approval + verification gates ───────────────────────────
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

        # Post-ACT verification gates (security + pytest + mypy)
        if changed_files:
            self._run_act_gates(ctx, changed_files)

        # Final report
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
        """Run ACT-phase verification gates: security + pytest + mypy."""
        click.echo(f"\n[VERIFY] Running ACT gates on {len(changed_files)} changed files...")

        # Security scan
        codes = {}
        for f in changed_files:
            if f.endswith((".py", ".js", ".ts", ".jsx", ".tsx")):
                content = ctx.get_file_content(f)
                if content:
                    codes[f] = content

        if codes:
            from verification.security_scan import SecurityScan
            scanner = SecurityScan()
            blocked = False
            blocking_issues = []
            for f, code in codes.items():
                scan_result = scanner.scan(code, file_path=f)
                if not scan_result.passed:
                    blocked = True
                    for finding in scan_result.findings:
                        blocking_issues.append(f"[SECURITY] {f}:{finding.line_number} - {finding.title}")
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
