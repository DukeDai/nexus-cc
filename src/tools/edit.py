"""EditTool - atomic string replacement in a file."""
from __future__ import annotations
from pathlib import Path


class EditTool:
    name = "Edit"
    description = "Replace old_string with new_string in a file."
    args_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def execute(self, *, path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict[str, object]:
        p = Path(path)
        text = p.read_text()
        count = text.count(old_string)
        if count == 0:
            raise ValueError(f"old_string not found in {path}")
        if not replace_all and count > 1:
            raise ValueError(f"old_string matches {count} locations; use replace_all=True")
        if replace_all:
            new_text = text.replace(old_string, new_string)
            replacements = count
        else:
            new_text = text.replace(old_string, new_string, 1)
            replacements = 1
        p.write_text(new_text)
        return {"path": str(p), "replacements": replacements}
