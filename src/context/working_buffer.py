"""Working Buffer — Git-worktree-like sandbox for code experiments.

Each buffer is an isolated directory under .nexus/buffers/<buffer_id>/
containing the original file and its current modified state.

This lets the agent experiment with code changes without affecting
the real project until changes are committed/applied.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


# ─── Data Types ────────────────────────────────────────────────────────────────

@dataclass
class BufferInfo:
    """Metadata for a working buffer."""
    buffer_id: str
    file_path: str          # original file path (relative)
    created_at: str
    updated_at: str
    original_hash: str      # hash of original content
    status: str = "active"  # "active" | "committed" | "discarded"


# ─── Working Buffer ─────────────────────────────────────────────────────────────

class WorkingBuffer:
    """Sandbox for experimental code changes.

    Usage:
        wb = WorkingBuffer(Path(".nexus/buffers"))

        # Stage a file for editing
        bid = wb.create_buffer("src/main.py", original_content)

        # Modify
        wb.write_buffer(bid, "def new_main(): pass")
        current = wb.read_buffer(bid)

        # See diff
        diff = wb.diff_buffer(bid)
        print(diff)

        # Apply changes to real file
        wb.apply_buffer(bid)

        # Or discard
        wb.delete_buffer(bid)
    """

    def __init__(self, buffers_root: Path | str = Path(".nexus/buffers")):
        self.buffers_root = Path(buffers_root)
        self.buffers_root.mkdir(parents=True, exist_ok=True)

    def _buffer_dir(self, buffer_id: str) -> Path:
        return self.buffers_root / buffer_id

    def _meta_file(self, buffer_id: str) -> Path:
        return self._buffer_dir(buffer_id) / "buffer.json"

    def _original_file(self, buffer_id: str) -> Path:
        return self._buffer_dir(buffer_id) / "original.txt"

    def _current_file(self, buffer_id: str) -> Path:
        return self._buffer_dir(buffer_id) / "current.txt"

    def _content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def create_buffer(self, file_path: str, original_content: str = "") -> str:
        """Stage a file for editing. Returns buffer_id.

        Args:
            file_path: Original file path (for reference)
            original_content: Content to stage for editing

        Returns:
            buffer_id (UUID prefix, first 8 chars)
        """
        buffer_id = uuid.uuid4().hex[:8]
        buf_dir = self._buffer_dir(buffer_id)
        buf_dir.mkdir(parents=True, exist_ok=True)

        # Write metadata
        meta = BufferInfo(
            buffer_id=buffer_id,
            file_path=file_path,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            original_hash=self._content_hash(original_content),
            status="active",
        )
        self._meta_file(buffer_id).write_text(json.dumps(asdict(meta), indent=2))
        self._original_file(buffer_id).write_text(original_content)
        self._current_file(buffer_id).write_text(original_content)

        return buffer_id

    def write_buffer(self, buffer_id: str, content: str) -> None:
        """Update the buffered (staged) content."""
        buf_dir = self._buffer_dir(buffer_id)
        if not buf_dir.exists():
            raise ValueError(f"Buffer {buffer_id} does not exist")

        self._current_file(buffer_id).write_text(content)

        # Update metadata
        meta = self._get_meta(buffer_id)
        meta.updated_at = datetime.now().isoformat()
        self._meta_file(buffer_id).write_text(json.dumps(asdict(meta), indent=2))

    def read_buffer(self, buffer_id: str) -> str:
        """Read the current buffered content."""
        cf = self._current_file(buffer_id)
        if not cf.exists():
            raise ValueError(f"Buffer {buffer_id} does not exist")
        return cf.read_text()

    def diff_buffer(self, buffer_id: str) -> str:
        """Return unified diff between original and current buffered content."""
        orig = self._original_file(buffer_id).read_text()
        curr = self._current_file(buffer_id).read_text()
        if orig == curr:
            return ""

        diff_lines = difflib.unified_diff(
            orig.splitlines(keepends=True),
            curr.splitlines(keepends=True),
            fromfile=f"{self._get_meta(buffer_id).file_path} (original)",
            tofile=f"{self._get_meta(buffer_id).file_path} (buffered)",
        )
        return "".join(diff_lines)

    def apply_buffer(self, buffer_id: str) -> str:
        """Apply buffered changes to the real file. Returns diff applied message.

        Creates parent directories as needed.
        """
        meta = self._get_meta(buffer_id)
        curr = self._current_file(buffer_id).read_text()
        real_path = Path(meta.file_path)

        # Create parent dirs
        real_path.parent.mkdir(parents=True, exist_ok=True)

        real_path.write_text(curr)
        return f"Applied buffer {buffer_id} → {real_path}"

    def commit_buffer(self, buffer_id: str, message: str = "") -> str:
        """Mark buffer as committed and optionally apply to real file."""
        meta = self._get_meta(buffer_id)
        meta.status = "committed"
        self._meta_file(buffer_id).write_text(json.dumps(asdict(meta), indent=2))
        return f"Buffer {buffer_id} committed: {message or 'no message'}"

    def list_buffers(self) -> list[BufferInfo]:
        """List all buffers."""
        buffers = []
        for buf_dir in self.buffers_root.iterdir():
            if buf_dir.is_dir() and (buf_dir / "buffer.json").exists():
                try:
                    meta = self._get_meta(buf_dir.name)
                    buffers.append(meta)
                except Exception:
                    continue
        return buffers

    def get_buffer(self, buffer_id: str) -> Optional[BufferInfo]:
        """Get buffer metadata, or None if not found."""
        try:
            return self._get_meta(buffer_id)
        except Exception:
            return None

    def delete_buffer(self, buffer_id: str) -> None:
        """Delete a buffer and all its files."""
        buf_dir = self._buffer_dir(buffer_id)
        if buf_dir.exists():
            shutil.rmtree(buf_dir)

    def switch_buffer(self, buffer_id: str) -> None:
        """Switch the current working state to a specific buffer.

        This saves the current state of the real file to a new buffer
        and switches to the specified buffer.
        """
        # For now just validate the buffer exists
        if not self._buffer_dir(buffer_id).exists():
            raise ValueError(f"Buffer {buffer_id} does not exist")

    def _get_meta(self, buffer_id: str) -> BufferInfo:
        meta_path = self._meta_file(buffer_id)
        if not meta_path.exists():
            raise ValueError(f"Buffer {buffer_id} does not exist")
        d = json.loads(meta_path.read_text())
        return BufferInfo(**d)
