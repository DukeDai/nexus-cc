"""`nexus tui` — Launch interactive TUI."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Callable

import click


@click.command()
@click.option("--task", "-t", help="Initial task description")
@click.option("--workdir", "-C", type=click.Path(file_okay=False), help="Working directory")
def tui(task: str | None, workdir: str | None) -> int:
    """Launch interactive TUI."""
    try:
        from tui.app import NexusTUI
    except ImportError as e:
        click.echo(f"TUI not available: {e}")
        click.echo("Run in CLI mode: nexus run --task '...'")
        return 1

    project_path = Path(workdir).expanduser().resolve() if workdir else Path.cwd()

    # Build task queue from --task argument
    if task:
        task_queue: list[dict[str, object]] = [{
            "id": f"task_{uuid.uuid4().hex[:8]}",
            "description": task,
            "priority": 2,
        }]
    else:
        task_queue = []

    # Context monitor - simple oscillating for demo, real impl would track token usage
    usage = [25.0, 35.0, 45.0, 55.0, 60.0, 65.0, 50.0, 40.0, 30.0]
    usage_index = [0]
    def context_monitor() -> float:
        val = usage[usage_index[0] % len(usage)]
        usage_index[0] += 1
        return val

    app = NexusTUI(
        task_queue=task_queue,
        context_monitor=context_monitor,
        checkpoint_dir=project_path / ".nexus" / "checkpoints",
        project_path=str(project_path),
    )
    app.run()
    return 0
