from src.agents.base import AgentRole, ModelTier
from src.agents.default_registry import register_default_roles
from src.agents.registry import RoleRegistry


class FakeRuntime:
    pass


def test_register_default_roles_returns_registry_with_4_roles():
    registry = register_default_roles(FakeRuntime())
    roles = registry.list_roles()
    assert AgentRole.SPECIFIER in roles
    assert AgentRole.IMPLEMENTER in roles
    assert AgentRole.REVIEWER in roles
    assert AgentRole.SECURITY in roles
    assert len(roles) == 4


def test_default_specifier_uses_sonnet_tier():
    registry = register_default_roles(FakeRuntime())
    defn = registry.get(AgentRole.SPECIFIER)
    assert defn.model_tier == ModelTier.SONNET
    assert "Read" in defn.allowed_tools


def test_default_security_uses_fast_tier():
    registry = register_default_roles(FakeRuntime())
    defn = registry.get(AgentRole.SECURITY)
    assert defn.model_tier == ModelTier.FAST


def test_default_implementer_has_max_subplan_steps_12():
    registry = register_default_roles(FakeRuntime())
    defn = registry.get(AgentRole.IMPLEMENTER)
    assert defn.max_subplan_steps == 12