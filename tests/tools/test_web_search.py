"""Tests for WebSearchTool stub."""
from __future__ import annotations

import pytest

from src.tools.web_search import WebSearchTool


@pytest.mark.asyncio
async def test_web_search_raises_not_implemented():
    """WebSearchTool.execute must raise NotImplementedError until wired."""
    tool = WebSearchTool()
    with pytest.raises(NotImplementedError, match="not yet wired"):
        await tool.execute(query="hello")


def test_web_search_metadata():
    """Tool exposes name, description, and args_schema."""
    tool = WebSearchTool()
    assert tool.name == "WebSearch"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.args_schema, dict)
    assert "query" in tool.args_schema["properties"]