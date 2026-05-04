"""`nexus session` — Session management commands."""

from __future__ import annotations

import click

from session import SessionManager, SessionStore


@click.group()
def session() -> None:
    """Session management commands."""
    pass


@session.command("list")
def list_sessions() -> None:
    """List all saved sessions."""
    store = SessionStore()
    sessions = store.list_sessions()
    if not sessions:
        click.echo("No sessions found.")
        return
    for s in sessions:
        click.echo(f"{s.get('session_id', '?')[:8]} | {s.get('created_at', '?')} | {s.get('status', '?')}")


@session.command("resume")
@click.argument("session_id")
@click.pass_context
def resume_session(ctx: click.Context, session_id: str) -> None:
    """Resume a saved session."""
    manager = SessionManager()
    data = manager.load(session_id)
    if data is None:
        click.echo(f"Session {session_id} not found.")
        ctx.exit(1)
    click.echo(f"Restored session {session_id}")
