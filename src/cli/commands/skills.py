"""`nexus skills` — Skills management commands."""

from __future__ import annotations

import click


@click.group()
def skills() -> None:
    """Skills management commands."""
    pass


@skills.command("list")
def list_skills() -> None:
    """List available skills."""
    click.echo("Skills system: use 'nexus skills list'")


@skills.command("add")
@click.argument("skill_name")
def add_skill(skill_name: str) -> None:
    """Add a new skill."""
    click.echo(f"Adding skill: {skill_name}")


@skills.command("remove")
@click.argument("skill_name")
def remove_skill(skill_name: str) -> None:
    """Remove a skill."""
    click.echo(f"Removing skill: {skill_name}")
