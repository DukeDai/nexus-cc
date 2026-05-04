#!/usr/bin/env python3
"""
Nexus — RalphLoop-driven Coding Agent (v5 Architecture)

This is the legacy entry point. All CLI commands are now in src/cli/.

Usage:
    python nexus.py --help          # Same as below
    python -m src.cli.main --help   # Direct module invocation
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── Add src/ to path so `from ralphloop import ...` resolves to `src/ralphloop` ─
_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    """Main entry point — delegates to the Click-based CLI."""
    from cli.main import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
