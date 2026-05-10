"""WAL (Write-Ahead Log) Protocol for RalphLoop crash recovery.

The WAL logs every state transition and tool execution BEFORE it happens,
providing a journal that can be replayed to recover from crashes.

Format: one JSON line per entry, files rotate at 1MB.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


# ─── Data Types ────────────────────────────────────────────────────────────────

@dataclass
class WALEntry:
    """A single WAL journal entry."""
    entry_type: str           # "transition" | "tool_call" | "tool_result" | "checkpoint"
    data: dict                # type-specific payload
    sequence: int             # monotonically increasing global sequence
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    wal_file: str = ""        # which file this entry belongs to

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str, wal_file: str = "") -> WALEntry:
        d = json.loads(line)
        return cls(
            entry_type=d["entry_type"],
            data=d["data"],
            sequence=d["sequence"],
            timestamp=d.get("timestamp", ""),
            wal_file=wal_file,
        )


# ─── WAL Manager ───────────────────────────────────────────────────────────────

MAX_WAL_SIZE_BYTES = 1 * 1024 * 1024  # 1MB


class WALManager:
    """Append-only Write-Ahead Log for RalphLoop crash recovery.

    Usage:
        wal = WALManager(Path(".nexus/wal"))
        wal.log_transition("PLAN", "ACT", "SPEC_VALID")
        wal.log_tool_call("read_file", {"path": "foo.py"}, "call_001")
        wal.log_tool_result("call_001", "file content...")
        # On crash recovery:
        entries = wal.recover()
    """

    def __init__(self, wal_dir: Path | str = Path(".nexus/wal")):
        self.wal_dir = Path(wal_dir)
        self.wal_dir.mkdir(parents=True, exist_ok=True)

        self._current_file: Path | None = None
        self._current_seq = 0
        self._closed = False

        # Find existing WAL files and resume from last sequence
        self._init_wal_file()

    def _init_wal_file(self) -> None:
        """Find or create the current WAL file and resume sequence."""
        existing = sorted(self.wal_dir.glob("wal_*.log"))
        if existing:
            self._current_file = existing[-1]
            # Read last sequence from this file
            try:
                lines = self._current_file.read_text().strip().split("\n")
                if lines:
                    last = WALEntry.from_json_line(lines[-1], str(self._current_file))
                    self._current_seq = last.sequence
            except Exception:
                self._current_seq = 0
        else:
            self._current_file = self.wal_dir / "wal_001.log"
            self._current_seq = 0
            self._current_file.touch()

    def _write_entry(self, entry: WALEntry) -> None:
        """Append a JSON line to the current WAL file."""
        if self._closed:
            raise RuntimeError("WALManager is closed")
        if self._current_file is None:
            self._init_wal_file()
        assert self._current_file is not None
        entry.wal_file = str(self._current_file)
        line = entry.to_json_line() + "\n"
        self._current_file.write_text(
            self._current_file.read_text() + line,
            encoding="utf-8",
        )
        self._current_seq += 1
        self.rotate_if_needed()

    def log_transition(
        self,
        from_state: str,
        to_state: str,
        trigger: str,
    ) -> int:
        """Log a RalphLoop state transition."""
        entry = WALEntry(
            entry_type="transition",
            data={
                "from_state": from_state,
                "to_state": to_state,
                "trigger": trigger,
            },
            sequence=self._current_seq + 1,
        )
        self._write_entry(entry)
        return entry.sequence

    def log_tool_call(
        self,
        tool_name: str,
        args: dict,
        tool_call_id: str,
    ) -> int:
        """Log a tool call (before execution)."""
        entry = WALEntry(
            entry_type="tool_call",
            data={
                "tool_name": tool_name,
                "args": args,
                "tool_call_id": tool_call_id,
            },
            sequence=self._current_seq + 1,
        )
        self._write_entry(entry)
        return entry.sequence

    def log_tool_result(
        self,
        tool_call_id: str,
        result: str,
        error: str | None = None,
    ) -> int:
        """Log a tool execution result (after execution)."""
        entry = WALEntry(
            entry_type="tool_result",
            data={
                "tool_call_id": tool_call_id,
                "result": result[:10000],  # Truncate very long results
                "error": error,
            },
            sequence=self._current_seq + 1,
        )
        self._write_entry(entry)
        return entry.sequence

    def log_checkpoint(
        self,
        state: str,
        context_summary: dict,
        task_index: int = 0,
        retry_count: int = 0,
    ) -> int:
        """Log a checkpoint (periodic state snapshot)."""
        entry = WALEntry(
            entry_type="checkpoint",
            data={
                "state": state,
                "task_index": task_index,
                "retry_count": retry_count,
                "context_summary": context_summary,
            },
            sequence=self._current_seq + 1,
        )
        self._write_entry(entry)
        return entry.sequence

    def recover(self) -> list[WALEntry]:
        """Recover all WAL entries from all WAL files.

        Returns entries sorted by sequence number (chronological order).
        """
        entries: list[WALEntry] = []
        for wal_file in sorted(self.wal_dir.glob("wal_*.log")):
            try:
                text = wal_file.read_text(encoding="utf-8")
                for line in text.strip().split("\n"):
                    if line.strip():
                        entries.append(
                            WALEntry.from_json_line(line, str(wal_file))
                        )
            except Exception:
                continue

        # Sort by sequence
        entries.sort(key=lambda e: e.sequence)
        return entries

    def get_recovery_plan(self) -> dict:
        """Analyze recovered entries and suggest recovery actions.

        Returns dict with:
            - transitions: list of state transitions
            - tool_calls: dict of tool_call_id -> (args, result)
            - last_checkpoint: most recent checkpoint entry
            - missing_results: tool_call_ids that were called but no result logged
        """
        entries = self.recover()
        transitions = []
        tool_calls: dict[str, dict] = {}
        tool_results: dict[str, dict] = {}
        checkpoints = []
        missing_results: list[str] = []

        for e in entries:
            if e.entry_type == "transition":
                transitions.append(e.data)
            elif e.entry_type == "tool_call":
                tc = e.data
                tool_calls[tc["tool_call_id"]] = {
                    "name": tc["tool_name"],
                    "args": tc["args"],
                    "sequence": e.sequence,
                }
            elif e.entry_type == "tool_result":
                tool_results[e.data["tool_call_id"]] = e.data
            elif e.entry_type == "checkpoint":
                checkpoints.append(e.data)

        # Find missing results
        for tc_id in tool_calls:
            if tc_id not in tool_results:
                missing_results.append(tc_id)

        last_checkpoint = checkpoints[-1] if checkpoints else None

        return {
            "transitions": transitions,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "missing_results": missing_results,
            "last_checkpoint": last_checkpoint,
            "total_entries": len(entries),
        }

    def rotate_if_needed(self) -> None:
        """Rotate WAL file if current file exceeds MAX_WAL_SIZE_BYTES."""
        if self._current_file is None:
            return
        try:
            size = self._current_file.stat().st_size
        except OSError:
            return
        if size >= MAX_WAL_SIZE_BYTES:
            self._rotate()

    def _rotate(self) -> None:
        """Close current WAL and open a new one."""
        # Find next sequence number
        existing = sorted(self.wal_dir.glob("wal_*.log"))
        next_num = len(existing) + 1
        self._current_file = self.wal_dir / f"wal_{next_num:03d}.log"
        self._current_file.touch()

    def flush(self) -> None:
        """Flush pending writes (no-op for single-file append, but API contract)."""
        pass

    def close(self) -> None:
        """Close the WAL manager."""
        self._closed = True

    def clear(self) -> None:
        """Delete all WAL files (for testing or after successful commit)."""
        self.close()
        for f in self.wal_dir.glob("wal_*.log"):
            f.unlink()
        self._current_file = self.wal_dir / "wal_001.log"
        self._current_seq = 0
        self._closed = False
        self._current_file.touch()
