"""`nexus skills` — Skills management commands."""

from __future__ import annotations

import json
from pathlib import Path

import click


DEFAULT_SKILLS_DIR = Path.home() / ".hermes" / "skills"


def _skill_files(skills_dir: Path) -> list[Path]:
    if not skills_dir.exists():
        return []
    return sorted(skills_dir.glob("recover-err-*.md"))


def _load_skill_meta(skill_path: Path) -> dict:
    try:
        text = skill_path.read_text()
        # Frontmatter-style parsing from the skill file
        meta: dict = {}
        for line in text.splitlines():
            if line.startswith("---"):
                break
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip().lower()] = val.strip()
        return meta
    except Exception:
        return {}


@click.group()
def skills() -> None:
    """Skills management commands."""
    pass


@skills.command("list")
@click.option("--skills-dir", default=None, help=f"Skills directory (default: {DEFAULT_SKILLS_DIR})")
def list_skills(skills_dir: str | None) -> None:
    """List all learned skills."""
    dir_path = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
    if not dir_path.exists():
        click.echo(f"No skills directory found: {dir_path}")
        return

    files = _skill_files(dir_path)
    if not files:
        click.echo("No skills learned yet.")
        return

    click.echo(f"Skills ({len(files)}):")
    for f in files:
        meta = _load_skill_meta(f)
        trigger = meta.get("trigger", f.stem)
        root_cause = meta.get("root_cause", "unknown")
        created = meta.get("created", "?")
        successes = meta.get("successes", "0")
        failures = meta.get("failures", "0")
        click.echo(f"  • {f.stem}")
        click.echo(f"    Trigger: {trigger}")
        click.echo(f"    Root cause: {root_cause} | Created: {created} | Successes: {successes} | Failures: {failures}")


@skills.command("show")
@click.argument("skill_name")
@click.option("--skills-dir", default=None, help=f"Skills directory")
def show_skill(skill_name: str, skills_dir: str | None) -> None:
    """Show skill details."""
    dir_path = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
    skill_path = dir_path / f"{skill_name}.md"
    if not skill_path.exists():
        # Try partial match
        matches = list(dir_path.glob(f"*{skill_name}*.md"))
        if matches:
            skill_path = matches[0]
        else:
            click.echo(f"Skill not found: {skill_name}")
            return

    click.echo(skill_path.read_text())


@skills.command("add")
@click.argument("skill_name")
@click.option("--trigger", default="", help="Error trigger pattern")
@click.option("--root-cause", default="", help="Root cause description")
@click.option("--recovery", default="", help="Recovery steps (comma-separated)")
@click.option("--skills-dir", default=None, help=f"Skills directory")
def add_skill(skill_name: str, trigger: str, root_cause: str, recovery: str, skills_dir: str | None) -> None:
    """Create a manual skill."""
    dir_path = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
    dir_path.mkdir(parents=True, exist_ok=True)

    recovery_steps = "\n".join(f"{i+1}. {step.strip()}" for i, step in enumerate(recovery.split(","))) if recovery else "1. Investigate and resolve"
    content = f"""---
name: {skill_name}
description: Manual skill
trigger: {trigger or skill_name}
root_cause: {root_cause or "unknown"}
created: 2026-05-27
successes: 0
failures: 0
tags: [manual]
---

# {skill_name}

**Root Cause:** {root_cause or "unknown"}

**Trigger:** `{trigger or skill_name}`

**Recovery Steps:**

{recovery_steps}
"""
    skill_path = dir_path / f"{skill_name}.md"
    skill_path.write_text(content)
    click.echo(f"Created skill: {skill_path}")


@skills.command("remove")
@click.argument("skill_name")
@click.option("--skills-dir", default=None, help=f"Skills directory")
def remove_skill(skill_name: str, skills_dir: str | None) -> None:
    """Remove a skill."""
    dir_path = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
    skill_path = dir_path / f"{skill_name}.md"
    if not skill_path.exists():
        # Try partial match
        matches = list(dir_path.glob(f"*{skill_name}*.md"))
        if matches:
            skill_path = matches[0]
        else:
            click.echo(f"Skill not found: {skill_name}")
            return

    skill_path.unlink()
    click.echo(f"Removed skill: {skill_path}")
