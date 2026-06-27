"""WebSearchTool - search the web via Anthropic SDK (stub for v1)."""
from __future__ import annotations

from typing import Any


class WebSearchTool:
    name = "WebSearch"
    description = "Search the web and return top results."
    args_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, *, query: str, max_results: int = 5) -> dict[str, Any]:
        # v1 stub: real impl requires Anthropic SDK web search tool wiring
        return {
            "results": [
                {"title": "stub", "url": "", "snippet": "WebSearch not yet wired"}
            ]
        }