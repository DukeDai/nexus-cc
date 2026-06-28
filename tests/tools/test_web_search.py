"""Tests for WebSearchTool."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.tools.web_search import WebSearchTool


def _make_result_block(*items: SimpleNamespace) -> SimpleNamespace:
    """Build a fake ``web_search_tool_result`` content block."""
    return SimpleNamespace(type="web_search_tool_result", content=list(items))


def _make_search_item(title: str, url: str) -> SimpleNamespace:
    """Build a fake ``web_search_result`` item."""
    return SimpleNamespace(
        type="web_search_result",
        title=title,
        url=url,
        encrypted_content="encrypted-blob",
    )


def test_web_search_metadata():
    """Tool exposes name, description, and args_schema."""
    tool = WebSearchTool()
    assert tool.name == "WebSearch"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.args_schema, dict)
    assert "query" in tool.args_schema["properties"]


@pytest.mark.asyncio
async def test_web_search_returns_results():
    """WebSearchTool.execute returns parsed results from web_search_tool_result blocks."""
    block = _make_result_block(
        _make_search_item("Result One", "https://example.com/one"),
        _make_search_item("Result Two", "https://example.com/two"),
    )
    text_block = SimpleNamespace(type="text", text="Some synthesized answer.")
    fake_message = SimpleNamespace(content=[block, text_block])
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=fake_message)))

    tool = WebSearchTool(client=fake_client)  # type: ignore[arg-type]
    out = await tool.execute(query="hello world", max_results=2)

    assert out == {
        "results": [
            {"title": "Result One", "url": "https://example.com/one"},
            {"title": "Result Two", "url": "https://example.com/two"},
        ]
    }

    fake_client.messages.create.assert_awaited_once()
    call_kwargs = fake_client.messages.create.await_args.kwargs
    assert call_kwargs["messages"] == [{"role": "user", "content": "hello world"}]
    assert call_kwargs["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 2}
    ]


@pytest.mark.asyncio
async def test_web_search_falls_back_to_answer_text():
    """When the model returns only text, surface it under ``answer``."""
    text_block = SimpleNamespace(type="text", text="Here is what I found.")
    fake_message = SimpleNamespace(content=[text_block])
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=fake_message)))

    tool = WebSearchTool(client=fake_client)  # type: ignore[arg-type]
    out = await tool.execute(query="anything")

    assert out == {"results": [], "answer": "Here is what I found."}


@pytest.mark.asyncio
async def test_web_search_handles_api_error():
    """API errors from the SDK propagate out of execute()."""
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(side_effect=RuntimeError("boom: api down")),
        )
    )

    tool = WebSearchTool(client=fake_client)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="boom: api down"):
        await tool.execute(query="hi")

    fake_client.messages.create.assert_awaited_once()