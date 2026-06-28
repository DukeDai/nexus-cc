from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from src.context.memory import MemoryStore, EpisodicEntry, EpisodicIndex, SemanticEntry, SemanticIndex, SkillIndex


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


def test_warm_skips_rebuild_when_wal_unchanged(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text('{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "x", "steps": []}}\n')
    wal = MagicMock()
    wal.path = wal_path
    store = MemoryStore(wal=wal, project_root=tmp_path)
    store.warm()
    rebuild_count_first = len(store.episodic()._entries)
    # Call warm again — no WAL change, should not re-read.
    store.warm()
    rebuild_count_second = len(store.episodic()._entries)
    assert rebuild_count_first == rebuild_count_second


def test_semantic_index_indexes_file_in_chunks(tmp_path):
    (tmp_path / "auth.py").write_text("def login():\n    pass\n" * 30)
    idx = SemanticIndex(project_root=tmp_path)
    idx.index_file(tmp_path / "auth.py")
    assert len(idx._chunks) >= 1
    assert all(c.path.name == "auth.py" for c in idx._chunks)


def test_semantic_search_returns_matching_chunks(tmp_path):
    (tmp_path / "auth.py").write_text("login function here\n" * 20)
    (tmp_path / "util.py").write_text("utility helper\n" * 20)
    idx = SemanticIndex(project_root=tmp_path)
    idx.index_file(tmp_path / "auth.py")
    idx.index_file(tmp_path / "util.py")
    results = idx.search("login function", k=5)
    assert len(results) >= 1
    assert results[0].path.name == "auth.py"


def test_semantic_search_with_embeddings_uses_cosine_similarity(tmp_path):
    (tmp_path / "auth.py").write_text("login function\n" * 10)
    (tmp_path / "util.py").write_text("utility helper\n" * 10)

    # Fake embedding function: returns a vector where auth.py chunks score higher for "login" query.
    def fake_embed(text: str) -> list[float]:
        if "login" in text.lower():
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]

    idx = SemanticIndex(project_root=tmp_path, embedding_fn=fake_embed)
    idx.index_file(tmp_path / "auth.py")
    idx.index_file(tmp_path / "util.py")
    # Embed all chunks
    for chunk in idx._chunks:
        chunk.embedding = fake_embed(chunk.content)
    query_vec = fake_embed("login function")
    results = idx.search_with_embeddings("login function", query_vec, k=5)
    assert len(results) >= 1
    assert results[0].path.name == "auth.py"


def test_semantic_search_falls_back_to_substring_without_embeddings(tmp_path):
    (tmp_path / "auth.py").write_text("login\n" * 5)
    idx = SemanticIndex(project_root=tmp_path, embedding_fn=None)
    idx.index_file(tmp_path / "auth.py")
    results = idx.search("login", k=5)
    assert len(results) >= 1


def test_skill_index_suggest_returns_matches():
    class FakeLoader:
        def search(self, query: str) -> list[Any]:
            return [{"name": "pytest_helper", "match_score": 0.9}]

    idx = SkillIndex(skill_loader=FakeLoader())
    suggestions = idx.suggest("add pytest fixture", plan=MagicMock())
    assert len(suggestions) == 1
    assert suggestions[0]["name"] == "pytest_helper"


def test_skill_index_apply_attaches_skill_to_step():
    idx = SkillIndex(skill_loader=MagicMock())
    skill = {"name": "pytest_helper", "template": "run pytest {path}"}
    step = MagicMock()
    result = idx.apply(skill, step)
    assert result is step
    step.attach_skill.assert_called_once_with(skill)


def test_planner_context_renders_three_sections(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text(
        '{"format_version": 2, "kind": "plan_start", "plan_id": "p1", "plan": {"id": "p1", "task": "add login", "steps": []}}\n'
        '{"format_version": 2, "kind": "plan_end", "plan_id": "p1", "outcome": "success"}\n'
    )
    (tmp_path / "convention.md").write_text("Use pytest for login testing")
    wal = MagicMock()
    wal.path = wal_path
    store = MemoryStore(wal=wal, project_root=tmp_path)
    store.semantic().index_file(tmp_path / "convention.md")
    store.warm()
    ctx = store.planner_context("add login", k=3)
    assert "Past similar tasks" in ctx
    assert "login" in ctx or "convention.md" in ctx
