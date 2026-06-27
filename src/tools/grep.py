"""GrepTool - regex search across files."""
from __future__ import annotations
import re
from pathlib import Path


class GrepTool:
    name = "Grep"
    description = "Regex search across files. Returns file:line:content matches."
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "include": {"type": "string"},
        },
        "required": ["pattern"],
    }

    async def execute(self, *, pattern: str, path: str = ".", include: str | None = None) -> dict[str, object]:
        regex = re.compile(pattern)
        matches: list[dict[str, object]] = []
        root = Path(path)
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if include and not p.match(include):
                continue
            try:
                lines = p.read_text().splitlines()
            except (UnicodeDecodeError, PermissionError):
                continue
            for i, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append({"path": str(p), "line": i, "content": line})
        return {"matches": matches}
