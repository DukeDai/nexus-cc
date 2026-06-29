"""Model router — selects model per ModelHint, emits cost records.

v1.2 refactor: heuristic TaskType selection is replaced by an explicit
ModelHint → model-name policy. v1.1 callers using `select_model(TaskType.X)`
are still supported as a thin wrapper that maps to `route(hint=ModelHint.PLANNER)`.

Supported model families:
- Anthropic (claude-haiku-4-5, claude-sonnet-4-6, claude-opus-4-8) — first-class.
- MiniMax via Anthropic-compatible API (MiniMax-M3, MiniMax-M2.7) — opt-in via
  .nexus/policy.yaml; uses Provider.MINIMAX_CN which routes to the user-configured
  ANTHROPIC_BASE_URL (or https://api.minimaxi.com/anthropic by default).
  Pricing is a rough estimate — override per-model via the `PRICING_PER_1K_TOKENS`
  table in cost_tracker.py if your contract differs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.llm.client import LLMClient, Provider
from src.llm.cost_tracker import CostTracker, estimate_cost
from src.llm.model_policy import DEFAULT_POLICY, ModelHint, ModelPolicy

logger = logging.getLogger(__name__)


# Backwards-compat enum — kept so v1.1 imports don't break. Not used in new code.
class TaskType:
    """Deprecated. Use ModelHint from src.llm.model_policy instead."""

    FAST = "fast"
    REASONING = "reasoning"
    CODE = "code"
    CREATIVE = "creative"
    ANALYSIS = "analysis"
    TOOL_USE = "tool_use"
    VISION = "vision"


@dataclass
class ModelConfig:
    """Metadata about a known model. Kept for the v1.1 DEFAULT_MODELS API."""

    name: str
    provider: Provider
    context_window: int = 200000
    supports_tools: bool = True
    supports_vision: bool = False
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    speed_factor: float = 1.0


class ModelRouter:
    """Hint-based model router with cost tracking.

    Args:
        policy: Resolved ModelPolicy (use ModelPolicy.load(...) in production).
        cost_tracker: CostTracker (or CostTracker.noop() for dry runs).
        api_keys: Optional {Provider: api_key} overrides (Anthropic key auto-pulled from env).
    """

    # v1.2 surface: Anthropic + MiniMax (Anthropic-compatible). Updated to current model ids.
    DEFAULT_MODELS: dict[str, ModelConfig] = {
        "claude-haiku-4-5": ModelConfig(
            name="claude-haiku-4-5",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.004,
            speed_factor=3.0,
        ),
        "claude-sonnet-4-6": ModelConfig(
            name="claude-sonnet-4-6",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            speed_factor=1.5,
        ),
        "claude-opus-4-8": ModelConfig(
            name="claude-opus-4-8",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.015,
            cost_per_1k_output=0.075,
            speed_factor=0.8,
        ),
        # MiniMax family — Anthropic-compatible API at https://api.minimaxi.com/anthropic.
        # Opt in by mapping hints to these names in .nexus/policy.yaml. Pricing is a
        # rough estimate (tier-equivalent to Sonnet 4.6); override per-model in
        # cost_tracker.PRICING_PER_1K_TOKENS if your contract differs.
        "MiniMax-M3": ModelConfig(
            name="MiniMax-M3",
            provider=Provider.MINIMAX_CN,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            speed_factor=1.5,
        ),
        "MiniMax-M2.7": ModelConfig(
            name="MiniMax-M2.7",
            provider=Provider.MINIMAX_CN,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.004,
            speed_factor=3.0,
        ),
    }

    def __init__(
        self,
        policy: ModelPolicy,
        cost_tracker: CostTracker,
        api_keys: dict[Provider, str] | None = None,
    ) -> None:
        self.policy = policy
        self.cost_tracker = cost_tracker
        self.api_keys = api_keys or {}
        self._clients: dict[str, LLMClient] = {}

    # ------------------------------------------------------------------ core

    def get_client(self, model_name: str) -> LLMClient:
        """Return a cached LLMClient for `model_name`. Raises if unknown."""
        if model_name not in self.DEFAULT_MODELS:
            raise ValueError(
                f"Unknown model: {model_name}. Available: {list(self.DEFAULT_MODELS)}"
            )
        if model_name in self._clients:
            return self._clients[model_name]

        config = self.DEFAULT_MODELS[model_name]
        api_key = self.api_keys.get(config.provider) or self._env_key()
        client = LLMClient(
            provider=config.provider,
            model=config.name,
            api_key=api_key,
        )
        self._clients[model_name] = client
        return client

    def _env_key(self) -> str:
        """Resolve an API key from env. Order:
        1. ANTHROPIC_API_KEY (Anthropic official)
        2. ANTHROPIC_AUTH_TOKEN (Anthropic-style, used by MiniMax)
        3. MINIMAX_API_KEY (MiniMax native)
        4. empty string (caller will fail loudly on auth error)
        """
        import os

        return (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("MINIMAX_API_KEY")
            or ""
        )

    def route(
        self,
        messages: list[dict],
        hint: ModelHint = ModelHint.PLANNER,
        role: str | None = None,
        tools: list[dict] | None = None,
        system_prompt: str = "",
        streaming: bool = False,
        callback: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> tuple[str, Any]:
        """Resolve model via policy → call → emit cost record → return (model, response)."""
        model_name = self.policy.resolve(hint, role)
        client = self.get_client(model_name)

        if streaming:
            response: Any = client.complete_streaming(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                callback=callback,
                **kwargs,
            )
        else:
            response = client.complete(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                **kwargs,
            )

        # Only emit cost records for non-streaming (we have usage there).
        if not streaming and getattr(response, "usage", None) is not None:
            from src.llm.cost_tracker import make_record

            usage = response.usage
            record = make_record(
                model=model_name,
                hint=hint,
                role=role,
                prompt_tokens=getattr(usage, "input_tokens", 0),
                completion_tokens=getattr(usage, "output_tokens", 0),
            )
            self.cost_tracker.emit(record)

        return model_name, response

    # ---------------------------------------------------------- v1.1 compat

    def select_model(
        self,
        task_type: Any = None,
        requires_tools: bool = False,
        requires_vision: bool = False,
        max_cost: Optional[float] = None,
        prefer_speed: bool = False,
        context_length: Optional[int] = None,
    ) -> str:
        """Deprecated v1.1 API.

        Returns the resolved PLANNER model from policy. Heuristic arguments
        are ignored — v1.2 routing is hint/policy driven, not capability-driven.
        Kept so v1.1 callers don't crash on import.
        """
        logger.debug(
            "ModelRouter.select_model is deprecated (v1.2); use route(hint=...). "
            "task_type=%s ignored",
            task_type,
        )
        return self.policy.resolve(ModelHint.PLANNER)

    # Backwards-compat `route(messages, task_type=...)` callers — accept legacy kwargs.
    def route_legacy(
        self,
        messages: list[dict],
        task_type: Any = None,
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        prefer_speed: bool = False,
        model_hint: Optional[str] = None,
        streaming: bool = False,
        callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, Any]:
        """v1.1 wrapper. `model_hint` wins, else falls back to PLANNER."""
        if model_hint:
            # Inject a one-shot cli_override into policy resolution.
            prior = self.policy.cli_override
            self.policy.cli_override = model_hint
            try:
                return self.route(
                    messages,
                    hint=ModelHint.PLANNER,
                    tools=tools,
                    system_prompt=system_prompt,
                    streaming=streaming,
                    callback=callback,
                )
            finally:
                self.policy.cli_override = prior
        return self.route(
            messages,
            hint=ModelHint.PLANNER,
            tools=tools,
            system_prompt=system_prompt,
            streaming=streaming,
            callback=callback,
        )

    def estimate_cost(self, model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Convenience pass-through to cost_tracker.estimate_cost."""
        return estimate_cost(model_name, input_tokens, output_tokens)

    def get_available_models(self, provider: Optional[Provider] = None) -> list[str]:
        if provider:
            return [m for m, c in self.DEFAULT_MODELS.items() if c.provider == provider]
        return list(self.DEFAULT_MODELS)

    def clear_client_cache(self) -> None:
        for c in self._clients.values():
            try:
                c.close()
            except Exception:
                pass
        self._clients.clear()