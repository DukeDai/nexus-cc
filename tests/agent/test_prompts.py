import json
import pytest
from datetime import datetime
from pathlib import Path
from src.agent.prompts import PromptTemplate, PromptTemplateRegistry


def test_prompt_template_round_trip():
    t = PromptTemplate(
        name="planner",
        system_prompt="You plan.",
        version=1,
        updated_at=datetime.now(),
        source_episodes=[],
        last_updated_walk_count=0,
    )
    assert t.version == 1
    assert t.last_updated_walk_count == 0


def test_registry_get_returns_current_version(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner",
        system_prompt="v1",
        version=1,
        updated_at=datetime.now(),
        source_episodes=[],
        last_updated_walk_count=0,
    ))
    t = reg.get("planner")
    assert t.system_prompt == "v1"
    assert t.version == 1


def test_registry_update_appends_to_history(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v1", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v2", version=2,
        updated_at=datetime.now(), source_episodes=["p1"], last_updated_walk_count=5,
    ))
    history = reg.history("planner")
    assert len(history) == 2
    assert history[0].version == 1
    assert history[1].version == 2


def test_registry_revert_copies_target_version(tmp_path):
    reg = PromptTemplateRegistry(path=tmp_path)
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v1", version=1,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v2", version=2,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.update("planner", PromptTemplate(
        name="planner", system_prompt="v3 bad", version=3,
        updated_at=datetime.now(), source_episodes=[], last_updated_walk_count=0,
    ))
    reg.revert("planner", target_version=1)
    current = reg.get("planner")
    assert current.system_prompt == "v1"
    assert current.version == 4
    assert current.last_updated_walk_count == 0   # reset on revert