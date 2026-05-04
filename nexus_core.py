#!/usr/bin/env python3
"""
Nexus Core — Compatibility shim for v3/v4 API.

Provides dict-based API compatibility for:
    - LLMClient(provider="auto") → auto-detect provider
    - client.complete(messages, tools) → dict response
    - ToolExecutor, TOOL_DEFINITIONS

All real logic delegates to src/ modules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ── src/ path ──────────────────────────────────────────────────────────────────
_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.llm.client import LLMClient as _RealLLMClient
from src.llm.client import Provider as _Provider, Response as _Response
from src.ralphloop.agent_loop import ToolExecutor, TOOL_DEFINITIONS


# ── Provider enum bridge ───────────────────────────────────────────────────────

class _ProviderCompat:
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"


# ── Shim LLMClient ─────────────────────────────────────────────────────────────

class LLMClient:
    """Compatibility shim for dict-based LLM API.
    
    Supports old API:
        client = LLMClient(provider="auto")
        response = client.complete(messages, tools)  # returns dict
    """

    def __init__(self, provider: str = "auto", model: str = ""):
        # Resolve "auto" to real provider
        if provider == "auto":
            provider = "anthropic"  # default to Anthropic for auto
        
        # Map string → Provider enum (use real enum from src.llm.client)
        _prov_map = {
            "anthropic": _Provider.ANTHROPIC,
            "openai": _Provider.OPENAI,
            "ollama": _Provider.OLLAMA,
        }
        prov_enum = _prov_map.get(provider, _Provider.ANTHROPIC)
        
        # Detect API key / base URL from settings.json (CC Switch) + env
        settings_env = {}
        settings_path = Path.home() / ".claude" / "settings.json"
        if settings_path.exists():
            try:
                import json as _json
                settings_env = _json.loads(settings_path.read_text()).get("env", {})
            except Exception:
                pass

        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY", "") or settings_env.get("ANTHROPIC_AUTH_TOKEN", "") or settings_env.get("ANTHROPIC_API_KEY", "")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "") or settings_env.get("ANTHROPIC_BASE_URL", "")
        detected_model = os.environ.get("ANTHROPIC_MODEL", "") or settings_env.get("ANTHROPIC_MODEL", "") or model
        
        self._real = _RealLLMClient(
            provider=prov_enum,
            model=detected_model or "claude-sonnet-4-20250514",
            api_key=api_key,
            base_url=base_url,
        )
        self.provider = prov_enum

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Call LLM and return dict response (old API).
        
        Returns: {"content": str, "tool_calls": [{"id", "name", "args"}]}
        """
        resp = self._real.complete(
            messages=messages,
            tools=tools,
        )
        # Normalize Response → dict
        if isinstance(resp, dict):
            return resp
        tool_calls = []
        for tc in (resp.tool_calls or []):
            if isinstance(tc, dict):
                tool_calls.append(tc)
            else:
                tool_calls.append({
                    "id": getattr(tc, "id", "") or "",
                    "name": getattr(tc, "name", "") or "",
                    "args": getattr(tc, "input", {}) or getattr(tc, "args", {}),
                })
        return {
            "content": resp.content or "",
            "tool_calls": tool_calls,
        }


class _StdoutStreamingCallback:
    """Print tokens to stdout in real-time during streaming."""
    def __call__(self, token: str):
        print(token, end="", flush=True)


# ── Shim NexusCore ─────────────────────────────────────────────────────────────

class NexusCore:
    """Minimal shim for legacy NexusCore class."""

    def __init__(self, workdir: str | Path | None = None, streaming: bool = False):
        from src.ralphloop.agent_loop import AgentLoopConfig
        self.workdir = Path(workdir or os.getcwd())
        self.llm = LLMClient(provider="auto")
        self.executor = ToolExecutor(workdir=self.workdir)
        self.streaming = streaming
        self.config = AgentLoopConfig(max_turns=50, streaming=streaming)

    def run_task(self, task: str) -> dict:
        """Run a task through the agent loop."""
        from src.ralphloop.agent_loop import run_agent_loop
        from src.ralphloop.implementation_context import ImplementationContext

        context = ImplementationContext(task=task, messages=[], tool_results=[], test_results=[], error_log=[])
        streaming_cb = _StdoutStreamingCallback() if self.streaming else None
        result = run_agent_loop(
            task=task,
            llm_client=self.llm,
            context=context,
            config=self.config,
            workdir=self.workdir,
            tools=TOOL_DEFINITIONS,
            streaming_callback=streaming_cb if streaming_cb else None,
        )
        return {
            "success": result.complete,
            "content": result.final_content,
            "turns": result.turns,
            "tool_calls": result.tool_calls,
        }
