"""Memory layer for Nexus v1.1.

Three indexes over the WAL JSONL (episodic), project files (semantic, opt-in
embeddings), and skill library (wraps src/skills/loader.py).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from src.context.wal import WALManager


@dataclass
class EpisodicEntry:
    """A single past Plan's outcome, derived from WAL."""

    plan_id: str
    plan_hash: str
    task: str
    outcome: Literal["success", "failed", "aborted"]
    duration_s: float
    step_count: int
    failed_step_ids: list[str]
    error_categories: list[str]
    created_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def plan_hash_of(plan_dict: dict[str, Any]) -> str:
        """Stable hash of a Plan's canonicalized dict form."""
        canonical = repr(sorted(plan_dict.items()))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass
class SemanticEntry:
    """A semantic chunk from a project file."""

    chunk_id: str
    path: Path
    start_line: int
    end_line: int
    content: str
    embedding: list[float] | None = None


class EpisodicIndex:
    """Derived view over WAL — never writes, only reads + caches."""

    def __init__(self, wal: WALManager, cache_path: Path):
        self._wal = wal
        self._cache_path = cache_path
        self._entries: dict[str, EpisodicEntry] = {}
        self._last_wal_mtime: float = 0.0

    def rebuild(self) -> None:
        """Scan WAL JSONL, build EpisodicEntry per completed plan, write cache."""
        plans: dict[str, dict[str, Any]] = {}
        completed_steps: dict[str, list[str]] = {}
        outcomes: dict[str, str] = {}
        durations: dict[str, float] = {}
        error_cats: dict[str, list[str]] = {}

        # Walk WAL file directly (EpisodicIndex is a derived view, not a WAL client).
        if not self._wal.path.exists():
            return
        for line in self._wal.path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            kind = rec.get("kind")
            plan_id = rec.get("plan_id")
            if kind == "plan_start":
                plans[plan_id] = rec.get("plan", {})
                completed_steps[plan_id] = []
                durations[plan_id] = rec.get("started_at", 0)
            elif kind == "step_complete":
                completed_steps.setdefault(plan_id, []).append(rec.get("cursor"))
                if rec.get("result", {}).get("error"):
                    error_cats.setdefault(plan_id, []).append(
                        rec["result"].get("error_category", "unknown")
                    )
            elif kind == "plan_end":
                outcomes[plan_id] = rec.get("outcome", "unknown")
                durations[plan_id] = rec.get("ended_at", 0) - durations[plan_id]

        self._entries.clear()
        for plan_id, plan_dict in plans.items():
            steps = plan_dict.get("steps", [])
            self._entries[plan_id] = EpisodicEntry(
                plan_id=plan_id,
                plan_hash=EpisodicEntry.plan_hash_of(plan_dict),
                task=plan_dict.get("task", ""),
                outcome=outcomes.get(plan_id, "unknown"),
                duration_s=durations.get(plan_id, 0.0),
                step_count=len(steps),
                failed_step_ids=[
                    s.get("id") for s in steps if s.get("id") not in completed_steps.get(plan_id, [])
                ],
                error_categories=error_cats.get(plan_id, []),
            )
        self._write_cache()
        self._last_wal_mtime = self._wal.path.stat().st_mtime if self._wal.path.exists() else 0.0

    def _write_cache(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._cache_path.open("w") as f:
            for entry in self._entries.values():
                f.write(json.dumps({
                    "plan_id": entry.plan_id,
                    "plan_hash": entry.plan_hash,
                    "task": entry.task,
                    "outcome": entry.outcome,
                    "duration_s": entry.duration_s,
                    "step_count": entry.step_count,
                    "failed_step_ids": entry.failed_step_ids,
                    "error_categories": entry.error_categories,
                    "created_at": entry.created_at.isoformat(),
                }) + "\n")

    def similar_past(self, task: str, k: int = 5) -> list[EpisodicEntry]:
        """Return top-k past plans by substring overlap with task."""
        if not self._entries:
            return []
        task_words = set(task.lower().split())
        scored = []
        for entry in self._entries.values():
            entry_words = set(entry.task.lower().split())
            overlap = len(task_words & entry_words)
            if overlap > 0:
                scored.append((overlap, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:k]]

    def success_rate(self, error_category: str) -> float:
        if not self._entries:
            return 0.0
        matching = [e for e in self._entries.values() if error_category in e.error_categories]
        if not matching:
            return 1.0
        return sum(1 for e in matching if e.outcome == "success") / len(matching)


class SemanticIndex:
    """Optional semantic memory — embeddings opt-in."""

    def __init__(self, project_root: Path, embedding_fn: Callable | None = None):
        self._root = project_root
        self._embed = embedding_fn
        self._chunks: list[SemanticEntry] = []

    def index_file(self, path: Path) -> None:
        """Read file, split into ~50-line chunks, store entries."""
        if not path.exists():
            return
        text = path.read_text(errors="replace")
        lines = text.splitlines()
        chunk_size = 50
        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i : i + chunk_size]
            if not chunk_lines:
                continue
            chunk_id = f"{path.name}:{i + 1}-{i + len(chunk_lines)}"
            self._chunks.append(
                SemanticEntry(
                    chunk_id=chunk_id,
                    path=path,
                    start_line=i + 1,
                    end_line=i + len(chunk_lines),
                    content="\n".join(chunk_lines),
                )
            )

    def search(self, query: str, k: int = 5) -> list[SemanticEntry]:
        """Return top-k chunks by substring/word overlap."""
        if not self._chunks:
            return []
        query_words = set(query.lower().split())
        scored = []
        for chunk in self._chunks:
            chunk_words = set(chunk.content.lower().split())
            overlap = len(query_words & chunk_words)
            if overlap > 0:
                scored.append((overlap, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [chunk for _, chunk in scored[:k]]


class SkillIndex:
    """Wraps existing src/skills/loader.py."""

    def __init__(self, skill_loader: Any | None = None):
        self._loader = skill_loader

    def suggest(self, task: str, plan: Any) -> list[Any]:
        # Filled in Task 15.
        return []

    def apply(self, skill: Any, step: Any) -> Any:
        # Filled in Task 15.
        return step


class MemoryStore:
    """Coordinates all three indexes + WAL sync."""

    def __init__(
        self,
        wal: WALManager,
        project_root: Path,
        *,
        embedding_fn: Callable | None = None,
        skill_loader: Any | None = None,
    ):
        self._wal = wal
        self._project_root = Path(project_root)
        cache_path = self._project_root / ".nexus" / "memory" / "episodic.jsonl"
        self._episodic_idx = EpisodicIndex(wal=wal, cache_path=cache_path)
        self._semantic_idx = SemanticIndex(project_root=self._project_root, embedding_fn=embedding_fn)
        self._skill_idx = SkillIndex(skill_loader=skill_loader)

    def warm(self) -> None:
        """Rebuild indexes only if WAL has changed since last warm."""
        if not self._wal.path.exists():
            return
        current_mtime = self._wal.path.stat().st_mtime
        if current_mtime > self._episodic_idx._last_wal_mtime:
            self._episodic_idx.rebuild()

    def episodic(self) -> EpisodicIndex:
        return self._episodic_idx

    def semantic(self) -> SemanticIndex:
        return self._semantic_idx

    def skills(self) -> SkillIndex:
        return self._skill_idx

    def planner_context(self, task: str, k: int = 5) -> str:
        """Render memory as context block to inject into Planner prompt."""
        # Filled in Task 16.
        return ""
