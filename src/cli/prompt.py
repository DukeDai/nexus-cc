"""nexus prompt <command> — manage prompt templates."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Inspect and manage prompt templates.")


def _registry(workdir: Path) -> "PromptTemplateRegistry":
    from src.agent.prompts import PromptTemplateRegistry
    return PromptTemplateRegistry(path=workdir / ".nexus" / "prompts")


@app.command("list")
def list_command(workdir: Path = typer.Option(Path("."), "--workdir", "-w")):
    reg = _registry(workdir)
    prompts_dir = workdir / ".nexus" / "prompts"
    if not prompts_dir.exists():
        typer.echo("No prompt templates registered.")
        return
    files = sorted(prompts_dir.glob("*.jsonl"))
    if not files:
        typer.echo("No prompt templates registered.")
        return
    for f in files:
        name = f.stem
        try:
            t = reg.get(name)
            typer.echo(f"{name} v{t.version} (updated {t.updated_at.date()})")
        except Exception:
            typer.echo(f"{name} (corrupt)")


@app.command("show")
def show_command(
    name: str,
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    reg = _registry(workdir)
    try:
        t = reg.get(name)
    except KeyError:
        typer.echo(f"Template {name!r} not found.")
        raise typer.Exit(1)
    typer.echo(f"# {name} v{t.version} (updated {t.updated_at.isoformat()})")
    typer.echo(t.system_prompt)


@app.command("history")
def history_command(
    name: str,
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    reg = _registry(workdir)
    history = reg.history(name)
    if not history:
        typer.echo(f"No history for {name!r}.")
        return
    for t in history:
        typer.echo(f"v{t.version} ({t.updated_at.date()}): walk_count={t.last_updated_walk_count}")


@app.command("revert")
def revert_command(
    target: str,  # format: name@version
    workdir: Path = typer.Option(Path("."), "--workdir", "-w"),
):
    if "@" not in target:
        typer.echo("Format: name@version (e.g., planner@2)")
        raise typer.Exit(1)
    name, version_str = target.rsplit("@", 1)
    version = int(version_str)
    reg = _registry(workdir)
    try:
        reverted = reg.revert(name, version)
    except (ValueError, KeyError) as e:
        typer.echo(f"Revert failed: {e}")
        raise typer.Exit(1)
    typer.echo(f"Reverted {name} to v{version} (now at v{reverted.version})")