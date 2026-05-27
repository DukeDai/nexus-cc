"""File search tool — search files by name pattern in a project."""

import fnmatch
from pathlib import Path

from pathlib import Path
import fnmatch

from src.engine.registry import BaseTool
from src.tools.base import ToolResult, ToolStatus


class FileSearchTool(BaseTool):
    """Search for files matching a glob pattern under a root directory.

    Usage:
        file_search(pattern="*.py", root="/path/to/project", max_results=20)
    """

    name = "file_search"
    description = "Recursively find files matching a glob pattern (e.g. *.py, *.md)"

    def execute(
        self,
        pattern: str = "*.py",
        root: str = ".",
        max_results: int = 50,
        **kwargs,
    ) -> ToolResult:
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            return ToolResult(message=f"Root path does not exist: {root}", success=False, status=ToolStatus.ERROR)

        matches = []
        for path in root_path.rglob(pattern):
            if path.is_file():
                rel = path.relative_to(root_path)
                matches.append(str(rel))
                if len(matches) >= max_results:
                    break

        if not matches:
            return ToolResult(message=f"No files matching '{pattern}' under {root}", success=False, status=ToolStatus.WARNING)

        message = f"Found {len(matches)} file(s):\n" + "\n".join(matches)
        return ToolResult(message=message, success=True, status=ToolStatus.SUCCESS)

    def __call__(self, **kwargs) -> ToolResult:
        return self.execute(**kwargs)
