from unittest.mock import MagicMock
from src.context.memory import MemoryStore, EpisodicEntry, SemanticEntry


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
