"""Tests for WebSearchTool stub."""
from __future__ import annotations

import pytest

from src.tools.web_search import WebSearchTool


@pytest.mark.asyncio
async def test_web_search_returns_stub():
    """WebSearchTool.execute should return the stub dict."""
    tool = WebSearchTool()
    result = await tool.execute(query="hello")
    assert isinstance(result, dict)
    assert "results" in result
    assert isinstance(result["results"], list)
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "stub"
    assert "WebSearch not yet wired" in result["results"][0]["snippet"]


def test_web_search_metadata():
    """Tool exposes name, description, and args_schema."""
    tool = WebSearchTool()
    assert tool.name == "WebSearch"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.args_schema, dict)
    assert "query" in tool.args_schema["properties"]