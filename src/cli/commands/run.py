"""`nexus run` — Execute a task through AgentRuntime (plan-first)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click

from src.agent.control import ControlChannel
from src.agent.runtime import AgentRuntime
from src.context.wal import WALManager
from src.tools.registry import ToolRegistry


@click.command()
@click.option("--task", "-t", required=True, help="Task description")
@click.option("--workdir", "-C", type=click.Path(file_okay=False), help="Working directory")
@click.option("--wal-path", type=click.Path(), help="WAL file path")
@click.option("--spec", "-s", help="Additional spec")
def run(task: str, workdir: str | None, wal_path: str | None, spec: str | None) -> int:
    """Run a task through AgentRuntime (plan-first architecture)."""
    project_path = Path(workdir or os.getcwd()).expanduser().resolve()
    wal_file = Path(wal_path).expanduser() if wal_path else (project_path / ".nexus" / "wal.jsonl")

    channel = ControlChannel()
    wal = WALManager(path=wal_file)
    tools = ToolRegistry.with_defaults(workdir=str(project_path))

    # LLM client — minimal stub for v1
    llm = _build_llm_client()
    if llm is None:
        click.echo("Error: ANTHROPIC_API_KEY not set and no LLM available", err=True)
        return 1

    runtime = AgentRuntime(
        llm=llm,
        tools=tools,
        verification=None,  # v1: optional
        wal=wal,
        channel=channel,
    )

    click.echo(f"Nexus | Task: {task[:80]}")
    click.echo(f"Project: {project_path}")

    # plan-then-walk
    async def run_async():
        plan = await runtime.plan(task, spec=spec)
        click.echo(f"Plan: {plan.spec}")
        click.echo(f"Steps: {len(plan.steps)}")
        results = await runtime.walk(plan)
        return results

    results = asyncio.run(run_async())

    failed = sum(1 for r in results if getattr(r, "status", None) == "failed")
    skipped = sum(1 for r in results if getattr(r, "status", None) == "skipped")
    done = sum(1 for r in results if getattr(r, "status", None) == "done")

    click.echo(f"\nResult: {done} done, {skipped} skipped, {failed} failed")
    return 0 if failed == 0 else 1


def _build_llm_client():
    """Build LLM client. v1: only Anthropic SDK supported.

    Returns None if no API key configured (caller should error gracefully).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        return None

    # Lazy import to avoid hard dep when not running
    try:
        from anthropic import AsyncAnthropic
        return _AnthropicLLM(AsyncAnthropic(api_key=api_key))
    except ImportError:
        return None


class _AnthropicLLM:
    """Minimal wrapper exposing .complete(system=, messages=)."""

    def __init__(self, client):
        self._client = client

    async def complete(self, *, system: str, messages: list[dict]) -> "_AnthropicResponse":
        msg = await self._client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        return _AnthropicResponse(msg)


class _AnthropicResponse:
    def __init__(self, msg):
        self.content = msg.content
