"""WebSearchTool - search the web via the Anthropic SDK server-side web search tool."""
from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


class WebSearchTool:
    """Run a web search through Anthropic's server-side ``web_search_20250305`` tool.

    The tool sends a single user-turn message with the web search tool enabled.
    The response contains ``web_search_tool_result`` blocks; each block holds a list
    of ``web_search_result`` items with ``title``/``url``/``encrypted_content`` etc.
    Any text blocks the model produced are aggregated as a fallback snippet.
    """

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

    # Default model used for the search call. Cheap + fast is fine here since
    # the heavy lifting is delegated to the web_search server tool.
    _DEFAULT_MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, client: "AsyncAnthropic | None" = None) -> None:
        # Allow injection for tests; fall back to a lazily-built client.
        self._client: "AsyncAnthropic | None" = client

    def _get_client(self) -> "AsyncAnthropic":
        if self._client is None:
            # Imported lazily so unit tests that inject a fake client don't need
            # the SDK installed at import time.
            from anthropic import AsyncAnthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            self._client = AsyncAnthropic(api_key=api_key)
        return self._client

    async def execute(self, *, query: str, max_results: int = 5) -> dict[str, Any]:
        client = self._get_client()

        message = await client.messages.create(
            model=self._DEFAULT_MODEL,
            max_tokens=1024,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": max(1, max_results),
                }
            ],
            messages=[{"role": "user", "content": query}],
        )

        results: list[dict[str, str]] = []
        text_snippets: list[str] = []

        for block in message.content:
            block_type = getattr(block, "type", None)
            if block_type == "web_search_tool_result":
                content = getattr(block, "content", None)
                # ``content`` is either a list of WebSearchResultBlock or an error object.
                if isinstance(content, list):
                    for item in content:
                        # Pydantic model -- access via attribute; fall back to dict.
                        title = getattr(item, "title", None)
                        url = getattr(item, "url", None)
                        if title is None and isinstance(item, dict):
                            title = item.get("title")
                        if url is None and isinstance(item, dict):
                            url = item.get("url")
                        if title and url:
                            results.append({"title": str(title), "url": str(url)})
            elif block_type == "text":
                text = getattr(block, "text", None)
                if text:
                    text_snippets.append(str(text))

        # If the model produced a synthesized answer but no raw search hits, fall
        # back to surfacing the answer text so the caller still gets useful output.
        if not results and text_snippets:
            return {
                "results": [],
                "answer": "\n".join(text_snippets),
            }

        return {"results": results}