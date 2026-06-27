"""ReadTool - read file contents with optional line range."""
from __future__ import annotations
from pathlib import Path


class ReadTool:
    name = "Read"
    description = "Read file contents. Optional line range via start_line/end_line (1-indexed, inclusive)."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "required": ["path"],
    }

    async def execute(self, *, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        p = Path(path)
        text = p.read_text()
        if start_line is None and end_line is None:
            return text
        lines = text.splitlines(keepends=True)
        start = (start_line or 1) - 1
        end = end_line if end_line is not None else len(lines)
        return "".join(lines[start:end])
