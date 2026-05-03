"""Nexus Glob Tool - File pattern matching."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .edit import BaseTool, ToolResult, ToolStatus


class GlobTool(BaseTool):
    """Tool for matching files using glob patterns.
    
    Supports recursive patterns like **/*.py and returns
    matching file paths as newline-separated strings.
    """
    
    name: str = "glob"
    description: str = "Match files using glob patterns"
    
    def execute(self, pattern: str, cwd: Optional[str] = None, max_results: int = 1000) -> ToolResult:
        """Execute glob pattern matching.
        
        Args:
            pattern: Glob pattern (e.g., "**/*.py", "src/**/*.ts")
            cwd: Working directory for the search (defaults to project root)
            max_results: Maximum number of results to return (default 1000)
            
        Returns:
            ToolResult containing newline-separated matching file paths
        """
        try:
            import glob as glob_module
            
            if cwd is None:
                cwd = os.path.expanduser("~/dev/nexus")
            
            search_path = os.path.join(cwd, pattern)
            
            matches = glob_module.glob(search_path, recursive=True)
            
            if len(matches) > max_results:
                matches = matches[:max_results]
            
            result_message = "\n".join(sorted(matches))
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=result_message,
                metadata={"pattern": pattern, "cwd": cwd, "count": len(matches)}
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Glob error: {str(e)}"
            )
