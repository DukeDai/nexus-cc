import pytest
from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry


def test_role_definition_construction():
    defn = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="You are a specifier.",
        allowed_tools=["Read", "Glob"],
        model_tier=ModelTier.SONNET,
        max_subplan_steps=8,
    )
    assert defn.role == AgentRole.SPECIFIER
    assert defn.allowed_tools == ["Read", "Glob"]
    assert defn.max_subplan_steps == 8


def test_role_registry_register_and_list():
    registry = RoleRegistry(runtime=None)
    defn = RoleDefinition(
        role=AgentRole.REVIEWER,
        system_prompt="Review code.",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    registry.register(AgentRole.REVIEWER, defn)
    assert registry.list_roles() == [AgentRole.REVIEWER]


def test_role_registry_register_overwrites():
    registry = RoleRegistry(runtime=None)
    defn1 = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="v1",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    defn2 = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="v2",
        allowed_tools=["Read", "Grep"],
        model_tier=ModelTier.OPUS,
    )
    registry.register(AgentRole.SPECIFIER, defn1)
    registry.register(AgentRole.SPECIFIER, defn2)
    assert registry.get(AgentRole.SPECIFIER).system_prompt == "v2"


def test_role_registry_get_missing_raises():
    registry = RoleRegistry(runtime=None)
    with pytest.raises(KeyError):
        registry.get(AgentRole.IMPLEMENTER)


def test_role_registry_register_mismatched_role_raises():
    registry = RoleRegistry(runtime=None)
    defn = RoleDefinition(
        role=AgentRole.REVIEWER,
        system_prompt="x",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    with pytest.raises(ValueError, match="does not match"):
        registry.register(AgentRole.SPECIFIER, defn)


def test_role_definition_default_on_subplan_failure_is_ask():
    defn = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="x",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    from src.agent.plan import OnFailure
    assert defn.on_subplan_failure == OnFailure.ASK


def test_role_definition_max_subplan_steps_default():
    defn = RoleDefinition(
        role=AgentRole.SPECIFIER,
        system_prompt="x",
        allowed_tools=["Read"],
        model_tier=ModelTier.SONNET,
    )
    assert defn.max_subplan_steps == 10
