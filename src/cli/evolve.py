"""nexus evolve — apply staged prompt updates without TUI interaction."""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(help="Apply or discard staged prompt updates.")


@app.command(name="evolve")
def evolve_command(
    auto: bool = typer.Option(False, "--auto", help="Apply without confirmation"),
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    staged = workdir / ".nexus" / "prompts" / "staged.json"
    if not staged.exists():
        typer.echo("No staged changes.")
        return
    data = json.loads(staged.read_text())
    if not auto:
        typer.echo(f"{len(data['changes'])} staged changes; use --auto to apply.")
        return
    from src.agent.prompts import PromptTemplate, PromptTemplateRegistry
    reg = PromptTemplateRegistry(path=workdir / ".nexus" / "prompts")
    for name, change in data["changes"].items():
        template = PromptTemplate(
            name=name,
            system_prompt=change["system_prompt"],
            version=change["version"],
            updated_at=__import__("datetime").datetime.fromisoformat(change["updated_at"]),
            source_episodes=change.get("source_episodes", []),
            last_updated_walk_count=change.get("last_updated_walk_count", 0),
        )
        reg.update(name, template)
        typer.echo(f"Applied {name} v{change['version']}")
    staged.unlink()
