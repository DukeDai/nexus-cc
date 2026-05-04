"""`nexus mcp` — MCP server management commands."""

from __future__ import annotations

import click


@click.group()
def mcp() -> None:
    """MCP server management commands."""
    pass


@mcp.command("list")
def list_servers() -> None:
    """List configured MCP servers."""
    try:
        from mcp import list_servers
        servers = list_servers()
        for s in servers:
            click.echo(f"{s['name']}: {s['command']}")
    except ImportError:
        click.echo("MCP system not fully wired.")


@mcp.command("presets")
def list_presets() -> None:
    """List available MCP server presets."""
    click.echo("Available presets: github, slack, postgres, filesystem")
