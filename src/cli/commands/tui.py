"""`nexus tui` — Launch interactive Textual TUI."""
from __future__ import annotations

import os
from pathlib import Path

import click

from src.agent.control import ControlChannel
from src.agent.runtime import AgentRuntime
from src.context.wal import WALManager
from src.tools.registry import ToolRegistry


@click.command()
@click.option("--workdir", "-C", type=click.Path(file_okay=False), help="Working directory")
@click.option("--wal-path", type=click.Path(), help="WAL file path")
def tui(workdir: str | None, wal_path: str | None) -> int:
    """Launch interactive Textual TUI."""
    project_path = Path(workdir or os.getcwd()).expanduser().resolve()
    wal_file = Path(wal_path).expanduser() if wal_path else (project_path / ".nexus" / "wal.jsonl")

    channel = ControlChannel()
    wal = WALManager(path=wal_file)
    tools = ToolRegistry.with_defaults(workdir=str(project_path))

    llm = _build_llm_client_or_none()
    runtime = AgentRuntime(
        llm=llm,
        tools=tools,
        verification=None,
        wal=wal,
        channel=channel,
    )

    from src.tui.app import NexusApp
    app = NexusApp(channel=channel, runtime=runtime, wal=wal)
    app.run()
    return 0


def _build_llm_client_or_none():
    """Same as run.py — local copy to avoid circular import."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        return None
    try:
        from anthropic import AsyncAnthropic
        return _LocalAnthropicLLM(AsyncAnthropic(api_key=api_key))
    except ImportError:
        return None


class _LocalAnthropicLLM:
    def __init__(self, client):
        self._client = client

    async def complete(self, *, system: str, messages: list[dict]):
        from anthropic import AsyncAnthropic
        msg = await self._client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        return _LocalAnthropicResponse(msg)


class _LocalAnthropicResponse:
    def __init__(self, msg):
        self.content = msg.content
