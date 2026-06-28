"""Nexus CLI main entry point — Click-based command dispatcher.

Usage:
    python -m nexus.cli.main run --task "..."
    python -m nexus.cli.main tui
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

# ── Ensure src/ is on path so `from ralphloop import ...` resolves ────────────
# Mirror legacy nexus.py: `sys.path.insert(0, str(Path(__file__).parent / "src"))`
# parents[2] = nexus-cc/src/  →  nexus-cc/src/  →  nexus-cc/
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from .commands import run, tui, session, mcp, skills, cost
from .memory import memory


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version="0.1.0", prog_name="nexus")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Nexus — RalphLoop-driven Coding Agent.

    Unified CLI for autonomous coding with self-correction and self-evolution.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Register command groups
cli.add_command(run)
cli.add_command(tui)
cli.add_command(session)
cli.add_command(mcp)
cli.add_command(skills)
cli.add_command(cost)
cli.add_command(memory)


def main() -> int:
    """Entry point for the CLI. Returns exit code."""
    try:
        result = cli(auto_envvar_prefix="NEXUS")
        return int(result) if result is not None else 0
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
