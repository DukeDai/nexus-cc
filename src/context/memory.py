"""Memory layer for Nexus v1.1.

Three indexes over the WAL JSONL (episodic), project files (semantic, opt-in
embeddings), and skill library (wraps src/skills/loader.py).
"""

from __future__ import annotations

import hashlib
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

    def rebuild(self) -> None:
        # Filled in Task 11.
        ...

    def similar_past(self, task: str, k: int = 5) -> list[EpisodicEntry]:
        # Filled in Task 11.
        return []

    def success_rate(self, error_category: str) -> float:
        # Filled in Task 11.
        return 0.0


class SemanticIndex:
    """Optional semantic memory — embeddings opt-in."""

    def __init__(self, project_root: Path, embedding_fn: Callable | None = None):
        self._root = project_root
        self._embed = embedding_fn
        self._chunks: list[SemanticEntry] = []

    def index_file(self, path: Path) -> None:
        # Filled in Task 13.
        ...

    def search(self, query: str, k: int = 5) -> list[SemanticEntry]:
        # Filled in Task 13.
        return []


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
        """Rebuild indexes from current state. Called on app startup."""
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
