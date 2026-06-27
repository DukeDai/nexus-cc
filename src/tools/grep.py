"""Nexus Grep Tool - Regex content search."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolResult, ToolStatus


class GrepTool(BaseTool):
    """Tool for searching file contents using regex patterns.
    
    Searches files in a directory and returns matches in the format:
    file:line:content (like grep -n)
    """
    
    name: str = "grep"
    description: str = "Search file contents using regex patterns"
    
    def execute(
        self,
        pattern: str,
        path: str = ".",
        file_glob: Optional[str] = None,
        context_lines: int = 0,
        max_results: int = 500
    ) -> ToolResult:
        """Execute regex search on file contents.
        
        Args:
            pattern: Regex pattern to search for
            path: Directory path to search in (default: ".")
            file_glob: Optional glob pattern to filter files (e.g., "*.py")
            context_lines: Number of lines before/after to include (default: 0)
            max_results: Maximum number of matches to return (default: 500)
            
        Returns:
            ToolResult containing matches in format: file:line:content
        """
        try:
            compiled_pattern = re.compile(pattern)
        except re.error as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Invalid regex pattern: {str(e)}"
            )
        
        try:
            if path == ".":
                search_path = os.path.expanduser("~/dev/nexus")
            else:
                search_path = os.path.expanduser(path)
            
            if not os.path.exists(search_path):
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=f"Path does not exist: {search_path}"
                )
            
            matches = []
            
            for root, dirs, files in os.walk(search_path):
                if file_glob:
                    import fnmatch
                    files = [f for f in files if fnmatch.fnmatch(f, file_glob)]
                
                for filename in files:
                    filepath = os.path.join(root, filename)
                    
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()
                            
                        for line_num, line_content in enumerate(lines, start=1):
                            if compiled_pattern.search(line_content):
                                if context_lines > 0:
                                    start_idx = max(0, line_num - context_lines - 1)
                                    end_idx = min(len(lines), line_num + context_lines)
                                    context = lines[start_idx:end_idx]
                                    match_str = f"{filepath}:{line_num}:{line_content.rstrip()}"
                                    
                                    if len(context) > 1:
                                        context_str = "".join(context).rstrip()
                                        match_str = f"{filepath}:{line_num}:\n{context_str}\n---"
                                else:
                                    match_str = f"{filepath}:{line_num}:{line_content.rstrip()}"
                                
                                matches.append(match_str)
                                
                                if len(matches) >= max_results:
                                    break
                    except (IOError, OSError):
                        continue
                    
                if len(matches) >= max_results:
                    break
                    
            result_message = "\n".join(matches)
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=result_message,
                metadata={
                    "pattern": pattern,
                    "path": search_path,
                    "file_glob": file_glob,
                    "context_lines": context_lines,
                    "count": len(matches)
                }
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Grep error: {str(e)}"
            )
