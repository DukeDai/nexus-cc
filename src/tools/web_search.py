"""Nexus Web Search Tool - Search the web using curl."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from .base import BaseTool, ToolResult, ToolStatus


class WebSearchTool(BaseTool):
    """Tool for searching the web using Bing or DuckDuckGo.
    
    Uses curl to perform searches and returns results in format:
    title | url | snippet
    """
    
    name: str = "web_search"
    description: str = "Search the web using Bing or DuckDuckGo"
    
    def __init__(self) -> None:
        super().__init__()
        self.project_root = os.path.expanduser("~/dev/nexus")
    
    def _search_duckduckgo(self, query: str, limit: int) -> ToolResult:
        """Search using DuckDuckGo HTML.
        
        Args:
            query: Search query
            limit: Maximum number of results
            
        Returns:
            ToolResult with formatted search results
        """
        try:
            import urllib.parse
            
            encoded_query = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "30", url],
                capture_output=True,
                text=True,
                timeout=35
            )
            
            if result.returncode != 0:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=f"curl failed: {result.stderr}"
                )
            
            html_content = result.stdout
            
            results = []
            title_pattern = re.compile(r'<a class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>')
            snippet_pattern = re.compile(r'<a class="result__snippet"[^>]*>([^<]*)</a>')
            
            titles = title_pattern.findall(html_content)
            snippets = snippet_pattern.findall(html_content)
            
            for i, (url, title) in enumerate(titles[:limit]):
                title = re.sub(r'<[^>]+>', '', title).strip()
                snippet = ""
                if i < len(snippets):
                    snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                
                results.append(f"{title} | {url} | {snippet}")
            
            message = "\n".join(results) if results else "No results found"
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=message,
                metadata={"query": query, "engine": "duckduckgo", "count": len(results)}
            )
            
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message="Search timed out"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Search error: {str(e)}"
            )
    
    def _search_bing(self, query: str, limit: int) -> ToolResult:
        """Search using Bing HTML.
        
        Args:
            query: Search query
            limit: Maximum number of results
            
        Returns:
            ToolResult with formatted search results
        """
        try:
            import urllib.parse
            
            encoded_query = urllib.parse.quote(query)
            url = f"https://www.bing.com/search?q={encoded_query}"
            
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "30", "-A", 
                 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", url],
                capture_output=True,
                text=True,
                timeout=35
            )
            
            if result.returncode != 0:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=f"curl failed: {result.stderr}"
                )
            
            html_content = result.stdout
            
            results = []
            bing_pattern = re.compile(
                r'<li class="b_algo"[^>]*>.*?<h2[^>]*><a[^>]*href="([^"]*)"[^>]*>([^<]*)</a></h2>.*?<p[^>]*>([^<]*)</p>',
                re.DOTALL
            )
            
            for match in bing_pattern.finditer(html_content):
                url = match.group(1).strip()
                title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
                snippet = re.sub(r'<[^>]+>', '', match.group(3)).strip()
                
                if title and url:
                    results.append(f"{title} | {url} | {snippet}")
                    
                if len(results) >= limit:
                    break
            
            message = "\n".join(results) if results else "No results found"
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=message,
                metadata={"query": query, "engine": "bing", "count": len(results)}
            )
            
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message="Search timed out"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Search error: {str(e)}"
            )
    
    def execute(self, query: str, limit: int = 5, engine: str = "duckduckgo") -> ToolResult:
        """Execute a web search.
        
        Args:
            query: Search query string
            limit: Maximum number of results to return (default: 5)
            engine: Search engine to use - "duckduckgo" or "bing" (default: duckduckgo)
            
        Returns:
            ToolResult containing results in format: title | url | snippet
        """
        if not query or not query.strip():
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message="Search query cannot be empty"
            )
        
        query = query.strip()
        
        if engine.lower() == "bing":
            return self._search_bing(query, limit)
        else:
            return self._search_duckduckgo(query, limit)
