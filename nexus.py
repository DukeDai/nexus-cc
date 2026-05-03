#!/usr/bin/env python3
"""
Nexus — Next-Generation Autonomous Coding Agent

RalphLoop-driven autonomous coding agent with:
- Multi-agent specialization (Specifier, Implementer, Reviewer, Security)
- Mandatory TDD enforcement
- MCP server integration
- Interactive TUI
- Session persistence
- Git worktree support
- Hook system
- CLAUDE.md hierarchy
"""

from __future__ import annotations

import argparse
import sys
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── Nexus source path ────────────────────────────────────────────────────────
NEXUS_SRC = Path(__file__).parent / "src"
sys.path.insert(0, str(NEXUS_SRC))

# ── Core imports ──────────────────────────────────────────────────────────────
from ralphloop import (
    RalphLoop,
    RalphState,
    ContextTier,
    Checkpoint,
    EscalationOption,
)
from context import ContextBudgetMonitor, BudgetTier, ClaudeMD, WorktreeManager
from agents import (
    SpecifierAgent,
    ImplementerAgent,
    ReviewerAgent,
    SecurityAgent,
    AgentResult,
)
from verification import VerificationPipeline
from skills import MistakeCapture, SkillAuthor, SkillLoader
from session import SessionManager, SessionStore
from hooks import HookManager, HookEvent
from tui import NexusTUI


# ═══════════════════════════════════════════════════════════════════════════════
# Shared utilities
# ═══════════════════════════════════════════════════════════════════════════════

def _colored(status: str, color: str) -> str:
    colors = {"green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m",
              "blue": "\033[94m", "reset": "\033[0m"}
    return f"{colors.get(color, '')}{status}{colors['reset']}"


def _print(msg: str, color: str = "reset") -> None:
    print(_colored(msg, color))


def _load_claudemd(project_path: str) -> Optional[ClaudeMD]:
    """Load CLAUDE.md hierarchy for the project."""
    try:
        loader = ClaudeMD(project_path=project_path)
        return loader.load()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Run command — execute RalphLoop
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_run(args: argparse.Namespace) -> int:
    """Execute RalphLoop with a task queue."""
    project_path = Path(args.project or Path.cwd()).resolve()
    _print(f"Nexus starting on: {project_path}", "blue")

    # Load CLAUDE.md context
    claudemd = _load_claudemd(str(project_path))
    if claudemd:
        _print(f"CLAUDE.md loaded: {len(claudemd.content)} chars", "green")

    # Build task queue
    if args.tasks_file:
        tasks = json.loads(Path(args.tasks_file).read_text())
    elif args.task:
        tasks = [{"description": args.task, "type": "generic"}]
    else:
        tasks = [{"description": "Implement task", "type": "generic"}]

    # Context budget monitor
    context_monitor_fn = lambda: 25.0  # Default budget estimate
    if args.max_context:
        cbm = ContextBudgetMonitor(max_context_tokens=args.max_context)
        # Estimate based on conversation length
        context_monitor_fn = lambda: 0.0  # Placeholder — real impl would track

    # Session manager
    session_store = SessionStore()
    session_mgr = SessionManager(
        project_path=str(project_path),
        store=session_store,
        checkpoint_dir=project_path / ".nexus" / "checkpoints",
    )

    # Verification pipeline
    verification_pipeline = VerificationPipeline(workdir=str(project_path))

    # Skill components
    mistake_capture = MistakeCapture()
    skill_author = SkillAuthor()
    skill_loader = SkillLoader(project_path=str(project_path))

    # Hook manager
    hook_mgr = HookManager()

    # Create RalphLoop
    def agent_executor(task: dict, phase: RalphState) -> dict:
        """Default agent executor — delegates to appropriate agent."""
        result = {"success": True, "error": None, "result": {}}

        if phase == RalphState.PLAN:
            spec_agent = SpecifierAgent()
            res = spec_agent.execute(task)
            result["success"] = res.success
            result["result"] = {"spec": res.output}

        elif phase == RalphState.ACT:
            impl_agent = ImplementerAgent()
            res = impl_agent.execute(task)
            result["success"] = res.success
            result["result"] = {"files": []}

        elif phase == RalphState.VERIFY:
            pipeline = VerificationPipeline(workdir=str(project_path))
            res = pipeline.run(task.get("files", []))
            result["success"] = res.get("passed", True)
            result["result"] = res

        elif phase == RalphState.REFLECT:
            mistake_capture.capture(task, result)
            result["result"] = {"learned": True}

        return result

    # Escalation handler
    def on_escalation(ctx: dict) -> EscalationOption:
        print(f"\n{'='*60}")
        print(f"ESCALATION: {ctx.get('error_log', ['Unknown error'])[-1]}")
        print(f"Retry count: {ctx.get('retry_count', 0)}")
        print(f"{'='*60}")
        print("Options: (1) FORCE_MERGE  (2) REWRITE  (3) ABANDON  (4) DECOMPOSE")
        choice = input("Select option [1-4]: ").strip()
        options = {
            "1": EscalationOption.FORCE_MERGE,
            "2": EscalationOption.REWRITE,
            "3": EscalationOption.ABANDON,
            "4": EscalationOption.DECOMPOSE,
        }
        return options.get(choice, EscalationOption.ABANDON)

    # Warning handler
    def on_warning(tier: ContextTier, msg: str) -> None:
        _print(f"WARNING [{tier.name}]: {msg}", "yellow")

    # State change handler
    def on_state_change(old: RalphState, new: RalphState) -> None:
        _print(f"  {old.name} → {new.name}", "blue")

    ralf = RalphLoop(
        task_queue=tasks,
        context_monitor=context_monitor_fn,
        checkpoint_dir=project_path / ".nexus" / "checkpoints",
        on_escalation=on_escalation,
        on_warning=on_warning,
        on_state_change=on_state_change,
        agent_executor=agent_executor,
    )

    # Run
    if args.tui:
        # TUI mode
        tui = NexusTUI(
            task_queue=tasks,
            context_monitor=context_monitor_fn,
            ralphloop=ralf,
        )
        _print("Launching TUI mode... (Ctrl+C to exit)", "blue")
        try:
            tui.run()
        except KeyboardInterrupt:
            print("\nShutting down TUI...")
        result = {"success": tui.state.is_running, "final_state": ralf.state}
    else:
        # CLI mode
        _print(f"Running RalphLoop with {len(tasks)} task(s)...", "blue")
        result = ralf.run()

    # Session save
    try:
        sm = SessionManager(project_path=str(project_path))
        session_id = sm.create(
            description=args.task or "Nexus run",
            tags=["nexus-run"],
            initial_task_queue=tasks,
        )
        sm.save(session_id, ralf)
        _print(f"Session saved: {session_id}", "green")
    except Exception as e:
        _print(f"Session save failed: {e}", "yellow")

    # Report
    if result.get("success"):
        _print(f"\n✓ RalphLoop completed successfully", "green")
        _print(f"  Final state: {result.get('final_state')}", "green")
    else:
        _print(f"\n✗ RalphLoop ended with state: {result.get('final_state')}", "red")

    return 0 if result.get("success") else 1


# ═══════════════════════════════════════════════════════════════════════════════
# Session command
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_session(args: argparse.Namespace) -> int:
    """Manage Nexus sessions."""
    store = SessionStore()
    mgr = SessionManager(store=store)

    if args.subcmd == "list":
        sessions = mgr.list(limit=args.limit)
        if not sessions:
            _print("No sessions found.", "yellow")
            return 0
        print(f"{'ID':<10} {'STATUS':<12} {'TASKS':<8} {'UPDATED':<25} {'DESCRIPTION'}")
        print("-" * 80)
        for s in sessions:
            status_color = {"active": "green", "completed": "blue",
                            "failed": "red", "paused": "yellow"}.get(s.status.value, "")
            print(f"{s.session_id:<10} {_colored(s.status.value, status_color):<12} "
                  f"{s.tasks_completed}/{s.task_count:<6} {s.updated_at:<25} {s.description}")
        print(f"\nTotal: {len(sessions)} session(s)")
        stats = mgr.get_stats()
        print(f"Stats: {stats['active']} active, {stats['completed']} completed, "
              f"{stats['failed']} failed, avg {stats['avg_tasks']:.1f} tasks")

    elif args.subcmd == "resume":
        data = mgr.load(args.id)
        if data is None:
            _print(f"Session {args.id} not found", "red")
            return 1
        _print(f"Restoring session {args.id}...", "blue")
        _print(f"  Description: {data.metadata.description}")
        _print(f"  State: {data.ralphloop.state} @ task {data.ralphloop.task_index}")
        _print(f"  Tasks: {data.metadata.tasks_completed}/{data.metadata.task_count}")
        _print(f"  Context: {data.context_usage_at_checkpoint:.1f}%")
        _print("Use 'nexus run' to continue — session will be auto-resumed", "yellow")

    elif args.subcmd == "delete":
        if mgr.delete(args.id):
            _print(f"Session {args.id} deleted", "green")
        else:
            _print(f"Session {args.id} not found", "red")
            return 1

    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# MCP command
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_mcp(args: argparse.Namespace) -> int:
    """Manage MCP servers."""
    from mcp import MCPPresets, MCPConfigManager

    mgr = MCPConfigManager()
    _print("MCP Server Management", "blue")

    if args.subcmd == "list":
        servers = mgr.list_servers()
        if not servers:
            _print("No MCP servers configured. Use 'nexus mcp add' to add one.", "yellow")
        else:
            print(f"{'NAME':<20} {'COMMAND':<30} {'STATUS'}")
            print("-" * 70)
            for name, cfg in servers.items():
                print(f"{name:<20} {str(cfg.get('command', ''))[:30]:<30} "
                      f"{_colored('configured', 'green')}")

    elif args.subcmd == "presets":
        print("Available MCP Presets:")
        for name in MCPPresets.list_presets():
            print(f"  - {name}")

    elif args.subcmd == "add":
        if args.preset:
            preset = MCPPresets.create(args.preset)
            mgr.add_server(args.name or args.preset, preset.config)
            _print(f"Added MCP server '{args.name or args.preset}' from preset", "green")
        else:
            _print("Specify --preset (github, slack, postgresql) or provide config", "yellow")

    elif args.subcmd == "remove":
        mgr.remove_server(args.name)
        _print(f"Removed MCP server '{args.name}'", "green")

    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# TUI command
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_tui(args: argparse.Namespace) -> int:
    """Launch the interactive TUI."""
    project_path = Path(args.project or Path.cwd()).resolve()
    _print(f"Launching Nexus TUI on: {project_path}", "blue")

    tasks = [{"description": "Interactive task", "type": "generic"}]
    if args.tasks_file:
        tasks = json.loads(Path(args.tasks_file).read_text())

    context_monitor_fn = lambda: 25.0
    tui = NexusTUI(
        task_queue=tasks,
        context_monitor=context_monitor_fn,
        ralphloop=None,
    )
    try:
        tui.run()
    except KeyboardInterrupt:
        print("\nTUI exited.")
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Worktree command
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_worktree(args: argparse.Namespace) -> int:
    """Manage git worktrees."""
    wtm = WorktreeManager(repo_path=args.repo or Path.cwd())

    if args.subcmd == "list":
        trees = wtm.list()
        if not trees:
            _print("No worktrees found.", "yellow")
        else:
            print(f"{'BRANCH':<25} {'PATH':<40} {'STATUS'}")
            print("-" * 80)
            for tree in trees:
                print(f"{tree.get('branch', ''):<25} "
                      f"{tree.get('path', ''):<40} "
                      f"{tree.get('status', 'ok')}")

    elif args.subcmd == "create":
        branch = args.branch
        path = args.path or f"../{branch}"
        result = wtm.create(path, branch)
        if result.get("success"):
            _print(f"Worktree created: {branch} at {path}", "green")
        else:
            _print(f"Failed: {result.get('error')}", "red")
            return 1

    elif args.subcmd == "remove":
        result = wtm.remove(args.path, force=args.force)
        if result.get("success"):
            _print(f"Worktree removed: {args.path}", "green")
        else:
            _print(f"Failed: {result.get('error')}", "red")
            return 1

    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Hooks command
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_hooks(args: argparse.Namespace) -> int:
    """Manage hook scripts."""
    mgr = HookManager()

    if args.subcmd == "list":
        hooks = mgr.list_hooks()
        if not hooks:
            _print("No hooks registered.", "yellow")
        else:
            print(f"{'EVENT':<25} {'SCRIPT':<40}")
            print("-" * 70)
            for event, scripts in hooks.items():
                for script in scripts:
                    print(f"{event:<25} {script:<40}")

    elif args.subcmd == "add":
        event = HookEvent(args.event) if args.event else None
        if event is None:
            print("Available events:")
            for e in HookEvent:
                print(f"  - {e.value}")
            return 0
        mgr.register(event, args.script)
        _print(f"Registered hook: {event.value} → {args.script}", "green")

    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Main CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus — Next-Generation Autonomous Coding Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="Nexus 0.2.0")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── run ──────────────────────────────────────────────────────────────────
    p_run = subparsers.add_parser("run", help="Run RalphLoop with task queue")
    p_run.add_argument("-t", "--task", help="Task description (or use --tasks-file)")
    p_run.add_argument("-f", "--tasks-file", help="JSON file with task queue")
    p_run.add_argument("-p", "--project", help="Project root path")
    p_run.add_argument("--tui", action="store_true", help="Launch TUI mode")
    p_run.add_argument("--max-context", type=int, help="Max context tokens")
    p_run.set_defaults(func=cmd_run)

    # ── session ───────────────────────────────────────────────────────────────
    p_sess = subparsers.add_parser("session", help="Session management")
    sess_sub = p_sess.add_subparsers(dest="subcmd", required=True)
    sess_list = sess_sub.add_parser("list", help="List sessions")
    sess_list.add_argument("--limit", type=int, default=20)
    sess_list.set_defaults(func=cmd_session)
    sess_resume = sess_sub.add_parser("resume", help="Resume a session")
    sess_resume.add_argument("id", help="Session ID")
    sess_resume.set_defaults(func=cmd_session)
    sess_del = sess_sub.add_parser("delete", help="Delete a session")
    sess_del.add_argument("id", help="Session ID")
    sess_del.set_defaults(func=cmd_session)

    # ── mcp ──────────────────────────────────────────────────────────────────
    p_mcp = subparsers.add_parser("mcp", help="MCP server management")
    mcp_sub = p_mcp.add_subparsers(dest="subcmd", required=True)
    mcp_list = mcp_sub.add_parser("list", help="List configured servers")
    mcp_list.set_defaults(func=cmd_mcp)
    mcp_presets = mcp_sub.add_parser("presets", help="List available presets")
    mcp_presets.set_defaults(func=cmd_mcp)
    mcp_add = mcp_sub.add_parser("add", help="Add an MCP server")
    mcp_add.add_argument("name", nargs="?", help="Server name")
    mcp_add.add_argument("--preset", choices=["github", "slack", "postgresql"],
                        help="Preset to add")
    mcp_add.set_defaults(func=cmd_mcp)
    mcp_remove = mcp_sub.add_parser("remove", help="Remove an MCP server")
    mcp_remove.add_argument("name", help="Server name")
    mcp_remove.set_defaults(func=cmd_mcp)

    # ── tui ──────────────────────────────────────────────────────────────────
    p_tui = subparsers.add_parser("tui", help="Launch interactive TUI")
    p_tui.add_argument("-p", "--project", help="Project root path")
    p_tui.add_argument("-f", "--tasks-file", help="JSON file with task queue")
    p_tui.set_defaults(func=cmd_tui)

    # ── worktree ──────────────────────────────────────────────────────────────
    p_wt = subparsers.add_parser("worktree", help="Git worktree management")
    wt_sub = p_wt.add_subparsers(dest="subcmd", required=True)
    wt_list = wt_sub.add_parser("list", help="List worktrees")
    wt_list.add_argument("--repo", help="Git repository path")
    wt_list.set_defaults(func=cmd_worktree)
    wt_create = wt_sub.add_parser("create", help="Create a worktree")
    wt_create.add_argument("branch", help="Branch name")
    wt_create.add_argument("--path", help="Worktree path")
    wt_create.add_argument("--repo", help="Git repository path")
    wt_create.set_defaults(func=cmd_worktree)
    wt_remove = wt_sub.add_parser("remove", help="Remove a worktree")
    wt_remove.add_argument("path", help="Worktree path")
    wt_remove.add_argument("--force", action="store_true")
    wt_remove.add_argument("--repo", help="Git repository path")
    wt_remove.set_defaults(func=cmd_worktree)

    # ── hooks ─────────────────────────────────────────────────────────────────
    p_hk = subparsers.add_parser("hooks", help="Hook management")
    hk_sub = p_hk.add_subparsers(dest="subcmd", required=True)
    hk_list = hk_sub.add_parser("list", help="List registered hooks")
    hk_list.set_defaults(func=cmd_hooks)
    hk_add = hk_sub.add_parser("add", help="Register a hook script")
    hk_add.add_argument("script", help="Path to hook script")
    hk_add.add_argument("--event", help="Event type (e.g., pre-tool-use)")
    hk_add.set_defaults(func=cmd_hooks)

    # ── Parse & dispatch ──────────────────────────────────────────────────────
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
