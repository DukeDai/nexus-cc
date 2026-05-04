"""Nexus Git Tool - Git command interface."""

from __future__ import annotations

import os
import subprocess
from typing import Optional, Any

from .edit import BaseTool, ToolResult, ToolStatus


class GitTool(BaseTool):
    """Tool for executing Git commands.
    
    Provides interface to common Git operations:
    status, add, commit, push, pull, branch_list, log, diff, stash
    """
    
    name: str = "git"
    description: str = "Execute Git commands"
    
    def __init__(self) -> None:
        super().__init__()
        self.project_root = os.path.expanduser("~/dev/nexus")
    
    def _run_git_command(self, args: list[str], cwd: Optional[str] = None) -> ToolResult:
        """Execute a git command and return the result.
        
        Args:
            args: Git command arguments (e.g., ["status"])
            cwd: Working directory (defaults to project root)
            
        Returns:
            ToolResult with command output or error
        """
        if cwd is None:
            cwd = self.project_root
        
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            output = result.stdout.strip() if result.stdout else result.stderr.strip()
            
            if result.returncode == 0:
                return ToolResult(
                    success=True,
                    status=ToolStatus.SUCCESS,
                    message=output,
                    metadata={"command": f"git {' '.join(args)}", "returncode": result.returncode}
                )
            else:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=output,
                    metadata={"command": f"git {' '.join(args)}", "returncode": result.returncode}
                )
                
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message="Git command timed out"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Git command failed: {str(e)}"
            )
    
    def execute(self, command: str, **kwargs: Any) -> ToolResult:
        """Execute a Git command.
        
        Args:
            command: One of: status, add, commit, push, pull, branch_list, log, diff, stash
            **kwargs: Additional arguments for specific commands:
                - add: files (list of files or patterns)
                - commit: message (commit message)
                - branch_list: None
                - log: max_count (limit number of commits)
                - diff: file (optional file to diff)
                - stash: action (e.g., "pop", "list", "save")
                
        Returns:
            ToolResult with command output
        """
        command = command.lower().strip()
        
        if command == "status":
            return self._run_git_command(["status"])
        
        elif command == "add":
            files = kwargs.get("files", ["."])
            if isinstance(files, str):
                files = [files]
            return self._run_git_command(["add"] + files)
        
        elif command == "commit":
            message = kwargs.get("message", "")
            if not message:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message="Commit message is required"
                )
            return self._run_git_command(["commit", "-m", message])
        
        elif command == "push":
            return self._run_git_command(["push"])
        
        elif command == "pull":
            return self._run_git_command(["pull"])
        
        elif command == "branch_list":
            return self._run_git_command(["branch", "-a"])
        
        elif command == "log":
            max_count = kwargs.get("max_count", 10)
            return self._run_git_command(["log", f"--oneline", f"-n{max_count}"])
        
        elif command == "diff":
            file_path = kwargs.get("file")
            if file_path:
                return self._run_git_command(["diff", file_path])
            return self._run_git_command(["diff"])
        
        elif command == "stash":
            action = kwargs.get("action", "list")
            if action == "list":
                return self._run_git_command(["stash", "list"])
            elif action == "save":
                message = kwargs.get("message", "")
                return self._run_git_command(["stash", "save"] + ([message] if message else []))
            elif action == "pop":
                return self._run_git_command(["stash", "pop"])
            elif action == "apply":
                return self._run_git_command(["stash", "apply"])
            else:
                return self._run_git_command(["stash", action])
        
        else:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Unknown git command: {command}"
            )
