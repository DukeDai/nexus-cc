"""`nexus cost` — Cost tracking report."""

from __future__ import annotations

import click


@click.command()
def cost() -> None:
    """Cost tracking report (Phase 2 feature)."""
    click.echo("Cost tracking — see ARCHITECTURE.md Phase 2")
