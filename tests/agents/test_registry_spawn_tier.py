"""Tests for RoleRegistry.spawn() consuming RoleDefinition.model_tier.

Verifies that spawn() reads definition.model_tier, maps it to a default
model name via the static _TIER_TO_DEFAULT_MODEL dict, and passes that
model_name to AgentRuntime.plan_subplan() as a hint.

Default OFF contract: when NEXUS_USE_MODEL_ROUTER is unset, the
model_name is still passed (it is a harmless hint to the LLM client),
but ModelPolicy doesn't re-resolve it. policy.yaml per_role overrides
take effect only when the router flag is set.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import AgentRole, ModelTier
from src.agents.registry import RoleDefinition, RoleRegistry, _TIER_TO_DEFAULT_MODEL


def _definition(role: AgentRole, tier: ModelTier) -> RoleDefinition:
    return RoleDefinition(
        role=role,
        system_prompt=f"You are {role.name}.",
        allowed_tools=["Read"],
        model_tier=tier,
        max_subplan_steps=4,
    )


class TestSpawnReadsModelTier:
    """spawn() reads definition.model_tier and forwards model_name."""

    @pytest.mark.asyncio
    async def test_spawn_sonnet_tier_passes_sonnet_model(self):
        """SONNET tier resolves to claude-sonnet-4-6 and reaches plan_subplan."""
        runtime = MagicMock()
        # plan_subplan is async; use AsyncMock so await works.
        runtime.plan_subplan = AsyncMock(return_value=MagicMock())

        registry = RoleRegistry(runtime=runtime)
        registry.register(AgentRole.IMPLEMENTER, _definition(AgentRole.IMPLEMENTER, ModelTier.SONNET))

        await registry.spawn(AgentRole.IMPLEMENTER, task="implement the thing")

        # Assert plan_subplan was called once with the right model_name.
        assert runtime.plan_subplan.await_count == 1
        call = runtime.plan_subplan.call_args
        assert call.kwargs["model_name"] == "claude-sonnet-4-6"
        assert call.kwargs["role"] == AgentRole.IMPLEMENTER
        assert call.kwargs["task"] == "implement the thing"
        # definition carries the tier
        assert call.kwargs["definition"].model_tier == ModelTier.SONNET

    @pytest.mark.asyncio
    async def test_spawn_fast_tier_passes_haiku_model(self):
        """FAST tier (e.g. SECURITY role) resolves to claude-haiku-4-5."""
        runtime = MagicMock()
        runtime.plan_subplan = AsyncMock(return_value=MagicMock())

        registry = RoleRegistry(runtime=runtime)
        registry.register(AgentRole.SECURITY, _definition(AgentRole.SECURITY, ModelTier.FAST))

        await registry.spawn(AgentRole.SECURITY, task="scan for vulns")

        call = runtime.plan_subplan.call_args
        assert call.kwargs["model_name"] == "claude-haiku-4-5"
        assert call.kwargs["definition"].model_tier == ModelTier.FAST

    @pytest.mark.asyncio
    async def test_spawn_opus_tier_passes_opus_model(self):
        """OPUS tier resolves to claude-opus-4-8."""
        runtime = MagicMock()
        runtime.plan_subplan = AsyncMock(return_value=MagicMock())

        registry = RoleRegistry(runtime=runtime)
        # Register a SPECIFIER at OPUS to exercise the OPUS branch via spawn.
        registry.register(AgentRole.SPECIFIER, _definition(AgentRole.SPECIFIER, ModelTier.OPUS))

        await registry.spawn(AgentRole.SPECIFIER, task="write the spec")

        call = runtime.plan_subplan.call_args
        assert call.kwargs["model_name"] == "claude-opus-4-8"
        assert call.kwargs["definition"].model_tier == ModelTier.OPUS


class TestTierMappingDict:
    """Static _TIER_TO_DEFAULT_MODEL mapping sanity checks."""

    def test_fast_maps_to_haiku(self):
        assert _TIER_TO_DEFAULT_MODEL[ModelTier.FAST] == "claude-haiku-4-5"

    def test_sonnet_maps_to_sonnet(self):
        assert _TIER_TO_DEFAULT_MODEL[ModelTier.SONNET] == "claude-sonnet-4-6"

    def test_opus_maps_to_opus(self):
        assert _TIER_TO_DEFAULT_MODEL[ModelTier.OPUS] == "claude-opus-4-8"

    def test_all_tiers_have_an_entry(self):
        for tier in ModelTier:
            assert tier in _TIER_TO_DEFAULT_MODEL


class TestSpawnRequiresRuntime:
    """spawn() still raises when no runtime is configured (pre-v1.2 contract)."""

    @pytest.mark.asyncio
    async def test_spawn_without_runtime_raises(self):
        registry = RoleRegistry(runtime=None)
        registry.register(AgentRole.IMPLEMENTER, _definition(AgentRole.IMPLEMENTER, ModelTier.SONNET))
        with pytest.raises(RuntimeError, match="requires a runtime"):
            await registry.spawn(AgentRole.IMPLEMENTER, task="x")
