"""Nexus CLI — Click-based command-line interface for Nexus.

Architecture:
    nexus.py (entry point) → cli.main (Click group) → cli.commands.* (subcommands)

All CLI commands are modular and independently testable.
"""

from click import Group

from .main import cli

__all__ = ["cli"]
