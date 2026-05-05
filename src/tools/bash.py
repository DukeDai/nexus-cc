"""Bash tool for executing shell commands."""

import re
import subprocess
import os
from typing import Optional

from .base import BaseTool, ToolResult


# Dangerous commands that are blacklisted
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"dd\s+if=",
    r":\(\)\{:\|:&\};:",  # fork bomb
    r"fork\s+-f",
    r"mkfs",
    r"ddof=",
]


def _is_dangerous(command: str) -> bool:
    """Check if a command matches any dangerous pattern."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return True
    return False


class BashTool(BaseTool):
    """Tool for executing bash commands safely."""

    name = "bash"
    description = "Execute bash commands"

    MAX_OUTPUT_SIZE = 50 * 1024  # 50KB

    def execute(self, command: str, timeout: int = 30, cwd: Optional[str] = None) -> ToolResult:
        """
        Execute a bash command with security and resource controls.

        Args:
            command: The command string to execute
            timeout: Maximum execution time in seconds (default: 30)
            cwd: Working directory for command execution

        Returns:
            ToolResult with success status, output data, or error message
        """
        # Security check
        if _is_dangerous(command):
            return ToolResult(
                success=False,
                error="Command blocked: potentially dangerous command detected"
            )

        # Validate timeout
        if timeout <= 0:
            return ToolResult(success=False, error="Timeout must be positive")

        try:
            # Validate cwd
            if cwd and not os.path.isdir(cwd):
                return ToolResult(success=False, error=f"Working directory does not exist: {cwd}")

            # 安全：使用 shell=False + shlex.split 避免 shell 注入
            import shlex
            cmd_list = shlex.split(command) if isinstance(command, str) else command
            result = subprocess.run(
                cmd_list,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                cwd=cwd,
                text=True,
                errors="replace",
            )

            # Output already decoded via text=True
            stdout = result.stdout
            stderr = result.stderr

            # Truncate output if needed
            if len(stdout) > self.MAX_OUTPUT_SIZE:
                stdout = stdout[:self.MAX_OUTPUT_SIZE] + "\n[OUTPUT TRUNCATED]"

            # Combine output
            output = stdout
            if stderr:
                output += "\n[STDERR]\n" + stderr

            return ToolResult(
                success=(result.returncode == 0),
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": result.returncode,
                } if result.returncode == 0 else None,
                error=output if result.returncode != 0 else None
            )

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Command timed out after {timeout} seconds")
        except Exception as e:
            return ToolResult(success=False, error=f"Execution error: {str(e)}")
