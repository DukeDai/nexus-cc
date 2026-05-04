#!/usr/bin/env python3
"""
Nexus — RalphLoop-driven Coding Agent (v5 Architecture)

Unified CLI entry point. All imports go through src/ packages.
RalphLoop: PLAN → ACT → VERIFY → REFLECT → (COMMIT|RETRY|ESCALATE|ABORT)
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import threading
from pathlib import Path
from datetime import datetime

# ── Nexus src path ────────────────────────────────────────────────────────────
_NEXUS_SRC = Path(__file__).parent / "src"
if str(_NEXUS_SRC) not in sys.path:
    sys.path.insert(0, str(_NEXUS_SRC))

# ── Core RalphLoop Engine ────────────────────────────────────────────────────
from ralphloop import (
    RalphLoop,
    RalphState,
    ContextTier,
    Checkpoint,
    RalphLoopMetrics,
    EscalationOption,
    TransitionTrigger,
    get_valid_transitions,
)
from ralphloop.implementation_context import ImplementationContext
from ralphloop.claude_md_loader import (
    load_claude_md,
    find_project_root,
    build_llm_system_prompt,
    get_project_context,
)
from ralphloop.agent_loop import (
    run_agent_loop,
    TOOL_DEFINITIONS,
    ToolExecutor,
    AgentLoopConfig,
)
from ralphloop.tdd_enforcer import TDDEnforcer
from llm.client import LLMClient, Provider
from llm.model_router import ModelRouter, TaskType
from self_evolution import SelfEvolutionEngine


# ═══════════════════════════════════════════════════════════════════════════════
# Provider / Model Detection
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_provider() -> tuple[Provider, str, str, str]:
    """Auto-detect best available LLM provider. Returns (provider, api_key, base_url, model)."""
    # Check Claude settings (CC Switch)
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_env = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings_env = json.load(f).get("env", {})
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


# ═══════════════════════════════════════════════════════════════════════════════
# RalphLoop Executor (connects orchestrator to agent_loop)
# ═══════════════════════════════════════════════════════════════════════════════

class RalphLoopExecutor:
    """Bridges RalphLoop orchestrator with run_agent_loop.

    The orchestrator handles state machine logic (transitions, retries, escalation).
    This class handles the actual LLM+tools execution by calling run_agent_loop.
    """

    def __init__(
        self,
        project_path: str,
        tdd_enabled: bool = True,
        system_prompt: str | None = None,
        streaming: bool = False,
    ):
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
        self.context = ImplementationContext(task="", messages=[], tool_results=[], test_results=[], error_log=[])
        # Self-Evolution: learn from errors permanently
        self.context._evolution_engine = SelfEvolutionEngine()

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

        # TDD enforcer (minimal, just tracks state)
        self.tdd_enforcer = TDDEnforcer() if self.tdd_enabled else None

        # RalphLoop orchestrator state
        self.state = RalphState.PLAN
        self.retries = 0
        self.MAX_RETRIES = 3
        self.turns = 0

    def execute_task(self, task: str) -> dict:
        """Execute a task through RalphLoop states.

        Returns dict with: success, turns, final_state, content, tool_count
        """
        self.context.task = task
        self.retries = 0
        self.turns = 0

        print(f"\n{'='*60}")
        print(f"Nexus RalphLoop | Task: {task[:60]}")
        print(f"Provider: {self.provider.value} | Model: {self.model}")
        print(f"Project: {self.project_path}")
        print(f"TDD: {'ON' if self.tdd_enabled else 'OFF'}")
        print(f"Streaming: {'ON' if self.streaming else 'OFF'}")
        print(f"{'='*60}\n")

        # Streaming callback for real-time token output
        def _streaming_cb(token: str):
            print(token, end="", flush=True)

        # Wrap run_agent_loop to inject streaming
        def _run_loop(task_prompt: str, state_name: str):
            if self.streaming:
                print(f"\n[{state_name}] ", end="", flush=True)
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
                print()  # newline after streaming output
            return result

        # ── PLAN state ──────────────────────────────────────────
        self.state = RalphState.PLAN
        print(f"[PLAN] Analyzing task...")
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
        print(f"\n[ACT] Executing plan...")
        act_prompt = f"TASK: {task}\n\nExecute the plan. Use tools to create/modify files, run tests, etc."
        result = _run_loop(act_prompt, "ACT")
        self.turns += result.turns

        # ── VERIFY state ───────────────────────────────────────
        self.state = RalphState.VERIFY
        print(f"\n[VERIFY] Checking results...")
        verify_prompt = (
            f"Verify the results of: {task}\n\n"
            f"Tool results so far:\n" + self._format_tool_results()
        )
        result = _run_loop(verify_prompt, "VERIFY")
        self.turns += result.turns

        # Check for errors in tool results
        if self._has_errors():
            if self.retries < self.MAX_RETRIES:
                self.retries += 1
                print(f"\n[!] Errors detected — retry {self.retries}/{self.MAX_RETRIES}")
                return self.execute_task(task)  # Retry
            else:
                print(f"\n[!!] Max retries exceeded")
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
        print(f"\n[REFLECT] Evaluating...")
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
        tool_results_content = " ".join(str(tr.get("result", "")) for tr in self.context.tool_results).lower()
        has_errors = any("error:" in str(tr.get("result", "")).lower() for tr in self.context.tool_results)
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

        print(f"\n{'='*60}")
        print(f"Final State: {final_state}")
        print(f"Total Turns: {self.turns}")
        print(f"Tool Calls: {len(self.context.tool_results)}")
        print(f"{'='*60}")

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

    def _handle_failure(self, phase: str, result) -> dict:
        print(f"\n[FAIL] {phase} failed: {result.final_content[:200] if result.final_content else 'no content'}")
        return {
            "success": False,
            "turns": self.turns,
            "final_state": "ABORT",
            "content": result.final_content,
            "tool_count": len(self.context.tool_results),
            "error": f"{phase} phase failed",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Commands
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_run(args: argparse.Namespace) -> int:
    """Run a task through RalphLoop."""
    project_path = Path(args.workdir or os.getcwd()).expanduser().resolve()

    # Load CLAUDE.md system prompt
    system_prompt = ""
    try:
        claude_md = load_claude_md(str(project_path))
        if claude_md:
            system_prompt = build_llm_system_prompt(str(project_path))
    except Exception as e:
        print(f"[WARN] Could not load CLAUDE.md: {e}", file=sys.stderr)

    # Execute
    executor = RalphLoopExecutor(
        project_path=str(project_path),
        tdd_enabled=args.tdd,
        system_prompt=system_prompt,
        streaming=args.stream,
    )

    result = executor.execute_task(args.task)

    print(f"\nResult: {json.dumps(result, indent=2, default=str)}")
    return 0 if result.get("success") else 1


def cmd_tui(args: argparse.Namespace) -> int:
    """Launch interactive TUI."""
    try:
        from tui.app import NexusTUIApp
        app = NexusTUIApp(workdir=args.workdir or os.getcwd())
        app.run()
    except ImportError as e:
        print(f"TUI not available: {e}")
        print("Run in CLI mode: nexus run --task '...'")
        return 1
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    """Session management."""
    from session import SessionManager, SessionStore
    store = SessionStore()
    if args.session_cmd == "list":
        sessions = store.list_sessions()
        if not sessions:
            print("No sessions found.")
            return 0
        for s in sessions:
            print(f"{s.get('session_id', '?')[:8]} | {s.get('created_at', '?')} | {s.get('status', '?')}")
        return 0
    elif args.session_cmd == "resume":
        manager = SessionManager()
        data = manager.load(args.session_id)
        if data is None:
            print(f"Session {args.session_id} not found.")
            return 1
        print(f"Restored session {args.session_id}")
        return 0
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """MCP server management."""
    if args.mcp_cmd == "list":
        try:
            from mcp import list_servers
            servers = list_servers()
            for s in servers:
                print(f"{s['name']}: {s['command']}")
        except ImportError:
            print("MCP system not fully wired.")
        return 0
    elif args.mcp_cmd == "presets":
        print("Available presets: github, slack, postgres, filesystem")
        return 0
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    """Skills management."""
    if args.skills_cmd == "list":
        print("Skills system: use 'nexus skills list'")
        return 0
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    """Cost tracking report (Phase 2 feature)."""
    print("Cost tracking — see ARCHITECTURE_v5.md Phase 2")
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nexus — RalphLoop Coding Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # nexus run
    p_run = subparsers.add_parser("run", help="Run a task through RalphLoop")
    p_run.add_argument("--task", "-t", required=True, help="Task description")
    p_run.add_argument("--workdir", "-C", help="Working directory")
    p_run.add_argument("--tdd/--no-tdd", dest="tdd", default=True, help="Enable TDD enforcement")
    p_run.add_argument("--stream/--no-stream", dest="stream", default=False, help="Enable streaming token output")
    p_run.set_defaults(func=cmd_run)

    # nexus tui
    p_tui = subparsers.add_parser("tui", help="Launch interactive TUI")
    p_tui.add_argument("--workdir", "-C", help="Working directory")
    p_tui.set_defaults(func=cmd_tui)

    # nexus session
    p_session = subparsers.add_parser("session", help="Session management")
    p_session.add_argument("session_cmd", choices=["list", "resume", "save"])
    p_session.add_argument("session_id", nargs="?", help="Session ID")
    p_session.set_defaults(func=cmd_session)

    # nexus mcp
    p_mcp = subparsers.add_parser("mcp", help="MCP server management")
    p_mcp.add_argument("mcp_cmd", choices=["list", "add", "presets"])
    p_mcp.add_argument("mcp_args", nargs="*")
    p_mcp.set_defaults(func=cmd_mcp)

    # nexus skills
    p_skills = subparsers.add_parser("skills", help="Skills management")
    p_skills.add_argument("skills_cmd", choices=["list", "add", "remove"])
    p_skills.add_argument("skill_name", nargs="?", help="Skill name")
    p_skills.set_defaults(func=cmd_skills)

    # nexus cost
    subparsers.add_parser("cost", help="Cost tracking report").set_defaults(func=cmd_cost)

    args = parser.parse_args()

    # Legacy: nexus --task "foo"
    if hasattr(args, "legacy_task") and args.legacy_task:
        args.task = args.legacy_task
        args.workdir = getattr(args, "legacy_workdir", None)
        args.tdd = getattr(args, "legacy_tdd", True)
        return cmd_run(args)

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
