"""GitTool - wrap git CLI for safe usage."""
from __future__ import annotations

import asyncio
from pathlib import Path


class GitTool:
    name = "Git"
    description = "Run git subcommands (status, diff, add, commit, log)."
    args_schema = {
        "type": "object",
        "properties": {
            "subcommand": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["subcommand"],
    }

    ALLOWED_SUBCOMMANDS = {
        "status",
        "diff",
        "add",
        "commit",
        "log",
        "show",
        "branch",
        "checkout",
    }

    def __init__(self, *, workdir: str) -> None:
        self._workdir = Path(workdir)

    async def execute(
        self, *, subcommand: str, args: list[str] | None = None
    ) -> dict[str, object]:
        if subcommand not in self.ALLOWED_SUBCOMMANDS:
            raise ValueError(f"git subcommand not allowed: {subcommand}")
        cmd = ["git", subcommand] + (args or [])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self._workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return {
            "exit_code": proc.returncode,
            "stdout": stdout_b.decode("utf-8", errors="replace"),
            "stderr": stderr_b.decode("utf-8", errors="replace"),
        }