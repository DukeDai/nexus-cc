"""`nexus session` — Plan session management via WAL."""
from __future__ import annotations

from pathlib import Path

import click


@click.group()
def session() -> None:
    """Session management commands (plan-first v1)."""
    pass


@session.command("list")
@click.option("--wal-path", type=click.Path(), help="WAL file path")
def list_sessions(wal_path: str | None) -> None:
    """List all plans found in WAL."""
    from src.context.wal import WALManager

    wal_file = Path(wal_path).expanduser() if wal_path else Path.cwd() / ".nexus" / "wal.jsonl"
    if not wal_file.exists():
        click.echo("No WAL found.")
        return

    # Read unique plan_ids from WAL
    plan_ids: set[str] = set()
    with wal_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                import json
                entry = json.loads(line)
                if entry.get("plan_id"):
                    plan_ids.add(entry["plan_id"])
            except Exception:
                continue

    if not plan_ids:
        click.echo("No plans in WAL.")
        return

    for pid in sorted(plan_ids):
        click.echo(pid)


@session.command("resume")
@click.argument("plan_id")
@click.option("--wal-path", type=click.Path(), help="WAL file path")
@click.option("--workdir", "-C", type=click.Path(file_okay=False), help="Working directory")
def resume_session(plan_id: str, wal_path: str | None, workdir: str | None) -> int:
    """Resume a plan by ID (loads WAL and continues remaining steps)."""
    from src.agent.control import ControlChannel
    from src.agent.runtime import AgentRuntime
    from src.context.wal import WALManager
    from src.tools.registry import ToolRegistry
    import asyncio

    project_path = Path(workdir or Path.cwd()).expanduser().resolve()
    wal_file = Path(wal_path).expanduser() if wal_path else (project_path / ".nexus" / "wal.jsonl")

    wal = WALManager(path=wal_file)
    completed = wal.get_completed_step_ids(plan_id)
    click.echo(f"Plan {plan_id}: {len(completed)} steps already complete")

    # Rebuild a minimal plan from WAL (in real impl, would persist plan_json too)
    recovered = asyncio.run(wal.recover())
    if recovered is None:
        click.echo("No checkpoint found.")
        return 1

    plan, last_cursor = recovered
    if plan.plan_id != plan_id:
        click.echo(f"WAL has plan {plan.plan_id}, not {plan_id}")
        return 1

    # In v1, we can only show status — full resume requires plan_json in WAL
    click.echo(f"Last cursor: {last_cursor}")
    click.echo("Full plan resume requires v1.1 — for now use `nexus tui` to see recovery modal.")
    return 0
