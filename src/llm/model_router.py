"""
Model router for selecting the appropriate LLM based on task requirements.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.llm.client import LLMClient, Provider

logger = logging.getLogger(__name__)


class TaskType(Enum):
    """Types of tasks that require different model capabilities."""
    FAST = "fast"  # Quick, simple tasks
    REASONING = "reasoning"  # Complex reasoning tasks
    CODE = "code"  # Code generation/editing
    CREATIVE = "creative"  # Creative writing
    ANALYSIS = "analysis"  # Data analysis
    TOOL_USE = "tool_use"  # Tasks requiring tool use
    VISION = "vision"  # Tasks requiring vision capabilities


@dataclass
class ModelConfig:
    """Configuration for a specific model."""
    name: str
    provider: Provider
    context_window: int = 200000
    supports_tools: bool = True
    supports_vision: bool = False
    cost_per_1k_input: float = 0.0  # In USD
    cost_per_1k_output: float = 0.0  # In USD
    speed_factor: float = 1.0  # Relative speed (higher = faster)


class ModelRouter:
    """
    Routes requests to appropriate LLM models based on task requirements.
    
    Supports:
    - Task-based routing (reasoning, code, fast, etc.)
    - Cost optimization
    - Provider fallback
    - Context window management
    """
    
    # Pre-configured model presets
    DEFAULT_MODELS = {
        # Anthropic models
        "claude-3-5-sonnet-20241022": ModelConfig(
            name="claude-3-5-sonnet-20241022",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            speed_factor=1.5,
        ),
        "claude-3-opus": ModelConfig(
            name="claude-3-opus",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.015,
            cost_per_1k_output=0.075,
            speed_factor=0.8,
        ),
        "claude-3-haiku": ModelConfig(
            name="claude-3-haiku",
            provider=Provider.ANTHROPIC,
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.00025,
            cost_per_1k_output=0.00125,
            speed_factor=3.0,
        ),
        # OpenAI models
        "gpt-4o": ModelConfig(
            name="gpt-4o",
            provider=Provider.OPENAI,
            context_window=128000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.01,
            speed_factor=1.2,
        ),
        "gpt-4o-mini": ModelConfig(
            name="gpt-4o-mini",
            provider=Provider.OPENAI,
            context_window=128000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
            speed_factor=2.5,
        ),
        "gpt-4-turbo": ModelConfig(
            name="gpt-4-turbo",
            provider=Provider.OPENAI,
            context_window=128000,
            supports_tools=True,
            supports_vision=True,
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
            speed_factor=1.0,
        ),
        "gpt-3.5-turbo": ModelConfig(
            name="gpt-3.5-turbo",
            provider=Provider.OPENAI,
            context_window=16385,
            supports_tools=True,
            supports_vision=False,
            cost_per_1k_input=0.0005,
            cost_per_1k_output=0.0015,
            speed_factor=4.0,
        ),
        # Ollama models (example configs, actual models may vary)
        "llama3": ModelConfig(
            name="llama3",
            provider=Provider.OLLAMA,
            context_window=8192,
            supports_tools=False,
            supports_vision=False,
            cost_per_1k_input=0.0,  # Local, no API cost
            cost_per_1k_output=0.0,
            speed_factor=2.0,  # Depends on local hardware
        ),
        "codellama": ModelConfig(
            name="codellama",
            provider=Provider.OLLAMA,
            context_window=16384,
            supports_tools=False,
            supports_vision=False,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            speed_factor=1.5,
        ),
        "mistral": ModelConfig(
            name="mistral",
            provider=Provider.OLLAMA,
            context_window=8192,
            supports_tools=False,
            supports_vision=False,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            speed_factor=2.5,
        ),
        # MiniMax models (via minimax-cn provider)
        "MiniMax-M2.7": ModelConfig(
            name="MiniMax-M2.7",
            provider=Provider.MINIMAX_CN,
            context_window=1000000,  # Large context
            supports_tools=True,
            supports_vision=False,
            cost_per_1k_input=0.0,   # Quota-based pricing
            cost_per_1k_output=0.0,
            speed_factor=2.0,
        ),
    }
    
    # Task to preferred model mapping
    TASK_PREFERENCES = {
        TaskType.FAST: ["gpt-3.5-turbo", "claude-3-haiku", "gpt-4o-mini", "llama3", "MiniMax-M2.7"],
        TaskType.REASONING: ["claude-3-5-sonnet-20241022", "gpt-4o", "claude-3-opus", "MiniMax-M2.7"],
        TaskType.CODE: ["claude-3-5-sonnet-20241022", "gpt-4o", "MiniMax-M2.7", "codellama"],
        TaskType.CREATIVE: ["claude-3-5-sonnet-20241022", "gpt-4o", "llama3", "MiniMax-M2.7"],
        TaskType.ANALYSIS: ["claude-3-5-sonnet-20241022", "gpt-4o", "claude-3-opus", "MiniMax-M2.7"],
        TaskType.TOOL_USE: ["claude-3-5-sonnet-20241022", "gpt-4o", "MiniMax-M2.7", "gpt-4o-mini"],
        TaskType.VISION: ["claude-3-5-sonnet-20241022", "gpt-4o", "claude-3-opus"],
    }
    
    def __init__(
        self,
        api_keys: dict[Provider, str] | None = None,
        base_urls: dict[Provider, str] | None = None,
        preferred_provider: Provider | None = None,
        custom_models: dict[str, ModelConfig] | None = None,
    ):
        """
        Initialize the ModelRouter.

        Args:
            api_keys: Dict of {Provider: api_key}. If a provider has no key,
                      it will be excluded from model selection.
            base_urls: Optional dict of {Provider: base_url} overrides.
            preferred_provider: If set, only use models from this provider
                               (useful when only one provider's key is available).
            custom_models: Optional dict of custom model configurations.
        """
        self.api_keys = api_keys or {}
        self.base_urls = base_urls or {}
        self.preferred_provider = preferred_provider
        self._clients: dict[str, LLMClient] = {}

        # Set default base URLs if not provided
        for provider in Provider:
            if provider not in self.base_urls:
                if provider == Provider.ANTHROPIC:
                    self.base_urls[provider] = "https://api.anthropic.com"
                elif provider == Provider.OPENAI:
                    self.base_urls[provider] = "https://api.openai.com/v1"
                elif provider == Provider.OLLAMA:
                    self.base_urls[provider] = "http://localhost:11434"
                elif provider == Provider.MINIMAX_CN:
                    self.base_urls[provider] = "https://api.minimaxi.com/anthropic"

        self.models = self.DEFAULT_MODELS.copy()
        
        # Add any custom models
        if custom_models:
            self.models.update(custom_models)
        
        # Cache of active clients
        self._clients: dict[str, LLMClient] = {}
    
    def get_client(self, model_name: str) -> LLMClient:
        """
        Get or create an LLM client for the specified model.
        
        Args:
            model_name: Name of the model
            
        Returns:
            Configured LLMClient instance
        """
        if model_name not in self.models:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(self.models.keys())}")
        
        if model_name in self._clients:
            return self._clients[model_name]
        
        config = self.models[model_name]
        
        client = LLMClient(
            provider=config.provider,
            model=config.name,
            api_key=self.api_keys.get(config.provider),
            base_url=self.base_urls.get(config.provider),
        )
        
        self._clients[model_name] = client
        return client
    
    def select_model(
        self,
        task_type: Optional[TaskType] = None,
        requires_tools: bool = False,
        requires_vision: bool = False,
        max_cost: Optional[float] = None,
        prefer_speed: bool = False,
        context_length: Optional[int] = None,
    ) -> str:
        """
        Select the best model for the given requirements.
        
        Args:
            task_type: Type of task
            requires_tools: Whether the task requires tool use
            requires_vision: Whether the task requires vision
            max_cost: Maximum cost per 1K tokens
            prefer_speed: Whether to prioritize speed over quality
            context_length: Required context window size
            
        Returns:
            Name of the selected model
        """
        candidates = list(self.models.keys())

        # Filter: only models from providers where we have API keys
        if self.api_keys:
            available_providers = set(self.api_keys.keys())
            candidates = [
                m for m in candidates
                if self.models[m].provider in available_providers
            ]

        # Filter: preferred provider
        if self.preferred_provider:
            candidates = [
                m for m in candidates
                if self.models[m].provider == self.preferred_provider
            ]

        # Filter by requirements
        if requires_tools:
            candidates = [m for m in candidates if self.models[m].supports_tools]
        
        if requires_vision:
            candidates = [m for m in candidates if self.models[m].supports_vision]
        
        if context_length:
            candidates = [m for m in candidates if self.models[m].context_window >= context_length]
        
        if max_cost is not None:
            # Filter by total cost per 1K tokens
            candidates = [
                m for m in candidates
                if (self.models[m].cost_per_1k_input + self.models[m].cost_per_1k_output) <= max_cost
            ]
        
        if not candidates:
            raise ValueError("No models available matching the specified requirements")
        
        # Sort candidates
        if task_type and task_type in self.TASK_PREFERENCES:
            preferences = self.TASK_PREFERENCES[task_type]

            def task_score(model: str) -> tuple[int, int, float]:
                """Score: (preference_tier, preference_rank, speed_factor).
                
                prefer_speed=True: sort by speed_factor DESC first (speed_tier 0 = fastest).
                prefer_speed=False: sort by preference first.
                """
                config = self.models[model]
                try:
                    rank = preferences.index(model)
                except ValueError:
                    rank = len(preferences)

                if prefer_speed:
                    # Speed primary: tier by speed bucket, then preference rank
                    speed_bucket = int(10 / config.speed_factor)  # higher speed = lower bucket
                    return (speed_bucket, rank, -config.speed_factor)
                else:
                    # Quality primary: preference rank, then cost
                    cost = config.cost_per_1k_input + config.cost_per_1k_output
                    return (rank, cost, 0)

            candidates.sort(key=task_score)
        elif prefer_speed:
            candidates.sort(key=lambda m: -self.models[m].speed_factor)
        else:
            candidates.sort(
                key=lambda m: self.models[m].cost_per_1k_input + self.models[m].cost_per_1k_output
            )

        selected = candidates[0]
        logger.debug(f"Selected model {selected} for task {task_type}")
        return selected
    
    def route(
        self,
        messages: list[dict],
        task_type: Optional[TaskType] = None,
        tools: Optional[list[dict]] = None,
        system_prompt: str = "",
        prefer_speed: bool = False,
        model_hint: Optional[str] = None,
        streaming: bool = False,
        callback=None,
    ) -> tuple[str, any]:
        """
        Route a request to the appropriate model and return the response.
        
        Args:
            messages: List of message dicts
            task_type: Type of task
            tools: List of tool definitions
            system_prompt: System prompt
            prefer_speed: Whether to prioritize speed
            model_hint: Specific model to use (overrides selection)
            streaming: Whether to use streaming
            callback: Streaming callback function
            
        Returns:
            Tuple of (model_name, Response)
        """
        # Determine if tools are required
        requires_tools = tools is not None and len(tools) > 0
        
        # Determine if vision might be needed (based on message content)
        requires_vision = False
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "image":
                            requires_vision = True
                            break
        
        # Select model
        if model_hint:
            model_name = model_hint
        else:
            model_name = self.select_model(
                task_type=task_type,
                requires_tools=requires_tools,
                requires_vision=requires_vision,
                prefer_speed=prefer_speed,
            )
        
        # Get client and make request
        client = self.get_client(model_name)
        
        if streaming:
            response = client.complete_streaming(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                callback=callback,
            )
        else:
            response = client.complete(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
            )
        
        return model_name, response
    
    def get_available_models(self, provider: Optional[Provider] = None) -> list[str]:
        """Get list of available models, optionally filtered by provider."""
        if provider:
            return [m for m, cfg in self.models.items() if cfg.provider == provider]
        return list(self.models.keys())
    
    def estimate_cost(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        Estimate the cost for a request.
        
        Args:
            model_name: Name of the model
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            
        Returns:
            Estimated cost in USD
        """
        if model_name not in self.models:
            raise ValueError(f"Unknown model: {model_name}")
        
        config = self.models[model_name]
        
        input_cost = (input_tokens / 1000) * config.cost_per_1k_input
        output_cost = (output_tokens / 1000) * config.cost_per_1k_output
        
        return input_cost + output_cost
    
    def clear_client_cache(self):
        """Clear all cached clients."""
        for client in self._clients.values():
            client.close()
        self._clients.clear()
