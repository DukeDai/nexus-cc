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
    """Bridges RalphLoop orchestrator with run_agent_loop."""

    def __init__(
        self,
        project_path: str,
        tdd_enabled: bool = True,
        system_prompt: str | None = None,
        streaming: bool = False,
    ) -> None:
        self.project_path = Path(project_path)
        self.tdd_enabled = tdd_enabled

        # Detect LLM provider
        self.provider, self.api_key, detected_base_url, settings_model = _detect_provider()
        self.model = _get_model_for_provider(self.provider, settings_model)

        # Store streaming preference
        self.streaming = streaming

        # LLM client — prefer detected base_url, fall back to env
        base_url = detected_base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self.llm = LLMClient(
            provider=self.provider,
            model=self.model,
            api_key=self.api_key,
            base_url=base_url,
        )

        # Model router for smart routing
        self.router = ModelRouter(api_keys={self.provider: self.api_key})

        # Context
        self.context = ImplementationContext(
            task="", messages=[], tool_results=[], test_results=[], error_log=[]
        )
        # Self-Evolution: learn from errors permanently
        self.context._evolution_engine = SelfEvolutionEngine()

        # Agent loop config
        from ralphloop.agent_loop import AgentLoopConfig
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

        # TDD enforcer (minimal, just tracks state)
        self.tdd_enforcer = TDDEnforcer() if self.tdd_enabled else None

        # RalphLoop orchestrator state
        self.state = RalphState.PLAN
        self.retries = 0
        self.MAX_RETRIES = 3
        self.turns = 0

    def execute_task(self, task: str) -> dict[str, Any]:
        """Execute a task through RalphLoop states."""
        self.context.task = task
        self.retries = 0
        self.turns = 0

        click.echo(f"\n{'='*60}")
        click.echo(f"Nexus RalphLoop | Task: {task[:60]}")
        click.echo(f"Provider: {self.provider.value} | Model: {self.model}")
        click.echo(f"Project: {self.project_path}")
        click.echo(f"TDD: {'ON' if self.tdd_enabled else 'OFF'}")
        click.echo(f"Streaming: {'ON' if self.streaming else 'OFF'}")
        click.echo(f"{'='*60}\n")

        # Streaming callback for real-time token output
        def _streaming_cb(token: str) -> None:
            click.echo(token, nl=False)

        # Wrap run_agent_loop to inject streaming
        def _run_loop(task_prompt: str, state_name: str) -> Any:
            if self.streaming:
                click.echo(f"\n[{state_name}] ", nl=False)
            result = run_agent_loop(
                task=task_prompt,
                llm_client=self.llm,
                context=self.context,
                config=self.config,
                system_prompt=self.system_prompt,
                workdir=self.project_path,
                tools=TOOL_DEFINITIONS,
                streaming_callback=_streaming_cb if self.streaming else None,
            )
            if self.streaming:
                click.echo()  # newline after streaming output
            return result

        # ── PLAN state ──────────────────────────────────────────
        self.state = RalphState.PLAN
        click.echo(f"[PLAN] Analyzing task...")
        plan_prompt = (
            f"TASK: {task}\n\n"
            f"Work directory: {self.project_path}\n\n"
            f"First, understand the task. Then respond with a brief plan "
            f"(what files to create/modify, what tools to use) before taking any action."
        )
        result = _run_loop(plan_prompt, "PLAN")
        self.turns += result.turns

        if not result.complete:
            return self._handle_failure("PLAN", result)

        # ── ACT state ───────────────────────────────────────────
        self.state = RalphState.ACT
        click.echo(f"\n[ACT] Executing plan...")
        act_prompt = f"TASK: {task}\n\nExecute the plan. Use tools to create/modify files, run tests, etc."
        result = _run_loop(act_prompt, "ACT")
        self.turns += result.turns

        # ── ACT 后验证 ─────────────────────────────────────────────
        changed_files = self.context.get_changed_files()
        if changed_files:
            click.echo(f"\n[VERIFY] Running security scan on {len(changed_files)} changed files...")
            from src.verification.security_scan import SecurityScan
            
            scanner = SecurityScan()
            codes = {}
            for f in changed_files:
                if f.endswith((".py", ".js", ".ts", ".jsx", ".tsx")):
                    content = self.context.get_file_content(f)
                    if content:
                        codes[f] = content
            
            if codes:
                # Run security scan on each file
                blocked = False
                blocking_issues = []
                for f, code in codes.items():
                    scan_result = scanner.scan(code, file_path=f)
                    if not scan_result.passed:  # If any issue found, block
                        blocked = True
                        for finding in scan_result.findings:
                            blocking_issues.append(f"[SECURITY] {f}:{finding.line_number} - {finding.title}")
                
                if blocked:
                    click.echo(f"\n[!!] Security scan FAILED:")
                    for issue in blocking_issues:
                        click.echo(f"  - {issue}")
                    if self.retries < self.MAX_RETRIES:
                        self.retries += 1
                        click.echo(f"[!] Retry {self.retries}/{self.MAX_RETRIES}")
                        return self.execute_task(task)
                    else:
                        return {
                            "success": False,
                            "turns": self.turns,
                            "final_state": "VERIFICATION_FAILED",
                            "content": self._format_tool_results(),
                            "tool_count": len(self.context.tool_results),
                            "error": f"Security blocked: {blocking_issues}",
                        }
                else:
                    click.echo("[OK] Security scan passed")

        # ── VERIFY state ───────────────────────────────────────
        self.state = RalphState.VERIFY
        click.echo(f"\n[VERIFY] Checking results...")
        verify_prompt = (
            f"Verify the results of: {task}\n\n"
            f"Tool results so far:\n{self._format_tool_results()}"
        )
        result = _run_loop(verify_prompt, "VERIFY")
        self.turns += result.turns

        # Check for errors in tool results
        if self._has_errors():
            if self.retries < self.MAX_RETRIES:
                self.retries += 1
                click.echo(f"\n[!] Errors detected — retry {self.retries}/{self.MAX_RETRIES}")
                return self.execute_task(task)  # Retry
            else:
                click.echo(f"\n[!!] Max retries exceeded")
                return {
                    "success": False,
                    "turns": self.turns,
                    "final_state": "ESCALATE",
                    "content": self._format_tool_results(),
                    "tool_count": len(self.context.tool_results),
                    "error": "Max retries exceeded",
                }

        # ── REFLECT state ───────────────────────────────────────
        self.state = RalphState.REFLECT
        click.echo(f"\n[REFLECT] Evaluating...")
        reflect_prompt = (
            f"REFLECT on the completed work for: {task}\n\n"
            f"Tool results:\n{self._format_tool_results()}\n\n"
            f"Is the task complete? Should you commit? Reply with:\n"
            f"  - Summary of what was done\n"
            f"  - Whether to COMMIT or continue working"
        )
        reflect_result = _run_loop(reflect_prompt, "REFLECT")
        self.turns += reflect_result.turns

        # Determine if done — more robust heuristic
        reflect_content = (reflect_result.final_content or "").lower()
        tool_results_content = " ".join(
            str(tr.get("result", "")) for tr in self.context.tool_results
        ).lower()
        has_errors = any(
            "error:" in str(tr.get("result", "")).lower() for tr in self.context.tool_results
        )
        # Success if: no errors AND (commit mentioned OR task appears complete)
        done = (
            not has_errors
            and (
                "commit" in reflect_content
                or ("done" in reflect_content and "not done" not in reflect_content)
                or ("complete" in reflect_content and "not complete" not in reflect_content)
                or ("finished" in reflect_content)
            )
        )
        final_state = "COMMIT" if done else "ACT"

        click.echo(f"\n{'='*60}")
        click.echo(f"Final State: {final_state}")
        click.echo(f"Total Turns: {self.turns}")
        click.echo(f"Tool Calls: {len(self.context.tool_results)}")
        click.echo(f"{'='*60}")

        return {
            "success": done,
            "turns": self.turns,
            "final_state": final_state,
            "content": reflect_result.final_content,
            "tool_count": len(self.context.tool_results),
        }

    def _has_errors(self) -> bool:
        for tr in self.context.tool_results:
            content = str(tr.get("result", ""))
            if "ERROR:" in content or "error:" in content:
                return True
        return False

    def _format_tool_results(self) -> str:
        lines = []
        for tr in self.context.tool_results[-10:]:
            name = tr.get("tool", "?")
            result = str(tr.get("result", ""))[:200]
            lines.append(f"[{name}] {result}")
        return "\n".join(lines) if lines else "(no tool results yet)"

    def _handle_failure(self, phase: str, result: Any) -> dict[str, Any]:
        click.echo(f"\n[FAIL] {phase} failed: {result.final_content[:200] if result.final_content else 'no content'}")
        return {
            "success": False,
            "turns": self.turns,
            "final_state": "ABORT",
            "content": result.final_content,
            "tool_count": len(self.context.tool_results),
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
