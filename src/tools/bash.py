"""BashTool - run shell commands with dangerous-pattern detection."""
from __future__ import annotations

import asyncio
import re


class DangerousCommandError(Exception):
    pass


DANGEROUS_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-?[a-zA-Z]*r[a-zA-Z]*\s+/\s",
    r"\bmkfs\.",
    r"\bdd\s+.*of=/dev/",
    r">\s*/dev/sd[a-z]",
    r"\bshutdown\b",
    r"\breboot\b",
    r":\(\)\s*\{.*\};\s*:",
]


class BashTool:
    name = "Bash"
    description = "Run a shell command and capture stdout/stderr/exit_code."
    args_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_s": {"type": "integer", "default": 60},
        },
        "required": ["command"],
    }

    async def execute(self, *, command: str, timeout_s: int = 60) -> dict[str, object]:
        for pat in DANGEROUS_PATTERNS:
            if re.search(pat, command):
                raise DangerousCommandError(f"command matches dangerous pattern: {pat}")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"exit_code": -1, "stdout": "", "stderr": "timeout"}
            return {
                "exit_code": proc.returncode,
                "stdout": stdout_b.decode("utf-8", errors="replace"),
                "stderr": stderr_b.decode("utf-8", errors="replace"),
            }
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}
