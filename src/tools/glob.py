"""GlobTool - find files matching a glob pattern."""
from __future__ import annotations
import glob as _glob
from pathlib import Path


class GlobTool:
    name = "Glob"
    description = "Find files matching a glob pattern. Recursive with **."
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
        },
        "required": ["pattern"],
    }

    async def execute(self, *, pattern: str, path: str = ".") -> dict[str, object]:
        full_pattern = str(Path(path) / pattern)
        matches = _glob.glob(full_pattern, recursive=True)
        return {"paths": sorted(matches)}
