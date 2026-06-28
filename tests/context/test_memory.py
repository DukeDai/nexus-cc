from pathlib import Path
from unittest.mock import MagicMock
from src.context.memory import MemoryStore, EpisodicEntry, EpisodicIndex, SemanticEntry


def test_memory_store_constructs_with_empty_indexes():
    wal = MagicMock()
    store = MemoryStore(wal=wal, project_root=MagicMock())
    assert store.episodic() is not None
    assert store.semantic() is not None
    assert store.skills() is not None


def test_episodic_entry_round_trip():
    entry = EpisodicEntry(
        plan_id="p_abc",
        plan_hash="hash123",
        task="add X",
        outcome="success",
        duration_s=12.0,
        step_count=5,
        failed_step_ids=[],
        error_categories=[],
    )
    assert entry.plan_hash == "hash123"
    assert entry.outcome == "success"


def test_episodic_index_rebuild_reads_from_wal(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "add login", "steps": [{"id": "s1"}, {"id": "s2"}]}}\n'
        '{"format_version": 2, "kind": "step_complete", "plan_id": "p1", "cursor": "s1", "result": {"status": "completed"}}\n'
        '{"format_version": 2, "kind": "step_complete", "plan_id": "p1", "cursor": "s2", "result": {"status": "completed"}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    wal = MagicMock()
    wal.path = wal_path
    idx = EpisodicIndex(wal=wal, cache_path=tmp_path / "cache.jsonl")
    idx.rebuild()
    assert len(idx._entries) == 1
    entry = list(idx._entries.values())[0]
    assert entry.plan_id == "p1"
    assert entry.task == "add login"
    assert entry.outcome == "success"
    assert entry.step_count == 2


def test_episodic_similar_past_returns_substring_matches(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "add login button", "steps": []}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p2", "plan": {"id": "p2", "task": "remove unused imports", "steps": []}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p2", "outcome": "failed"}\n'
    )
    wal = MagicMock()
    wal.path = wal_path
    idx = EpisodicIndex(wal=wal, cache_path=tmp_path / "cache.jsonl")
    idx.rebuild()
    matches = idx.similar_past("add login screen", k=5)
    assert len(matches) >= 1
    assert matches[0].plan_id == "p1"
