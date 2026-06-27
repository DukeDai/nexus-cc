"""WriteTool - write content to file (creates parent dirs)."""
from __future__ import annotations
from pathlib import Path


class WriteTool:
    name = "Write"
    description = "Write content to a file. Creates parent directories if needed."
    args_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def execute(self, *, path: str, content: str) -> dict[str, object]:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"path": str(p), "bytes": len(content)}
