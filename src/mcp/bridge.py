"""MCP Tool Bridge.

Bridges MCP tools to Nexus actions with caching, rate limiting, and
tool-to-action conversion. Provides a high-level interface for calling
MCP tools with automatic retry, result caching, and request throttling.

Usage:
    bridge = MCPToolBridge(connection_manager)

    # Call an MCP tool with automatic caching
    result = await bridge.call("github", "list_issues", {"limit": 5})

    # Convert tool result to action
    action = bridge.tool_to_action("github", "create_issue", result)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class RateLimitScope(Enum):
    """Scope for rate limit tracking."""

    GLOBAL = auto()
    """Rate limit applies to all servers."""

    PER_SERVER = auto()
    """Rate limit applies per server."""

    PER_TOOL = auto()
    """Rate limit applies per tool per server."""


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting.

    Attributes:
        max_requests: Maximum requests allowed in the window.
        window_seconds: Time window in seconds.
        scope: Whether limit is global, per-server, or per-tool.
        retry_after: Seconds to wait before retrying after limit hit.
    """

    max_requests: int = 60
    window_seconds: int = 60
    scope: RateLimitScope = RateLimitScope.PER_SERVER
    retry_after: int = 5


@dataclass
class CacheEntry:
    """Cached tool call result.

    Attributes:
        value: The cached result.
        created_at: When the entry was created.
        expires_at: When the entry expires.
        hit_count: Number of times this entry was used.
    """

    value: Any
    created_at: datetime
    expires_at: datetime
    hit_count: int = 0

    @property
    def is_expired(self) -> bool:
        """Check if the cache entry has expired."""
        return datetime.now() > self.expires_at


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting.

    Attributes:
        tokens: Current available tokens.
        last_refill: Timestamp of last token refill.
        config: Rate limit configuration.
    """

    tokens: float
    last_refill: datetime
    config: RateLimitConfig

    def __post_init__(self):
        """Initialize bucket with full tokens."""
        self.tokens = float(self.config.max_requests)
        self.last_refill = datetime.now()

    def consume(self) -> bool:
        """Attempt to consume one token.

        Returns:
            True if token was consumed, False if rate limited.
        """
        self._refill()

        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = datetime.now()
        elapsed = (now - self.last_refill).total_seconds()

        if elapsed > 0:
            tokens_to_add = (elapsed / self.config.window_seconds) * self.config.max_requests
            self.tokens = min(self.config.max_requests, self.tokens + tokens_to_add)
            self.last_refill = now

    @property
    def wait_time(self) -> float:
        """Seconds to wait until a token is available."""
        if self.tokens >= 1:
            return 0.0
        deficit = 1 - self.tokens
        tokens_per_second = self.config.max_requests / self.config.window_seconds
        return deficit / tokens_per_second


@dataclass
class MCPToolBridgeConfig:
    """Configuration for the MCP Tool Bridge.

    Attributes:
        cache_enabled: Enable result caching.
        cache_ttl: Default cache TTL in seconds.
        cache_max_entries: Maximum cached entries per server.
        rate_limit: Default rate limit configuration.
        max_retries: Maximum retry attempts for failed calls.
        retry_delay: Base delay between retries in seconds.
        timeout: Default tool call timeout in seconds.
    """

    cache_enabled: bool = True
    cache_ttl: int = 300
    cache_max_entries: int = 100
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    max_retries: int = 3
    retry_delay: float = 1.0
    timeout: int = 120


class MCPToolBridge:
    """Bridge for calling MCP tools with caching and rate limiting.

    Features:
        - Automatic result caching with TTL
        - Per-server and per-tool rate limiting
        - Retry with exponential backoff
        - Tool-to-action conversion
        - Request coalescing for concurrent identical calls

    Usage:
        bridge = MCPToolBridge(connection_manager)

        # Simple tool call with caching
        result = await bridge.call("github", "list_issues", {"limit": 5})

        # Force refresh cached result
        result = await bridge.call("github", "list_issues", {"limit": 5}, refresh=True)

        # Convert result to actionable response
        action = bridge.tool_to_action("github", "list_issues", result)
    """

    def __init__(
        self,
        connection_manager: Any,
        config: Optional[MCPToolBridgeConfig] = None,
    ):
        """Initialize the tool bridge.

        Args:
            connection_manager: MCPConnectionManager instance.
            config: Optional bridge configuration.
        """
        self._manager = connection_manager
        self._config = config or MCPToolBridgeConfig()
        self._cache: dict[str, CacheEntry] = {}
        self._rate_limits: dict[str, RateLimitBucket] = {}
        self._in_flight: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def _make_cache_key(
        self,
        server: str,
        tool: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> str:
        """Generate a cache key for a tool call.

        Args:
            server: Server name.
            tool: Tool name.
            arguments: Tool arguments.

        Returns:
            Hashed cache key.
        """
        # Normalize arguments for consistent hashing
        args_str = json.dumps(arguments or {}, sort_keys=True, default=str)
        raw = f"{server}:{tool}:{args_str}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _make_rate_limit_key(
        self,
        server: str,
        tool: Optional[str] = None,
        scope: Optional[RateLimitScope] = None,
    ) -> str:
        """Generate a rate limit key.

        Args:
            server: Server name.
            tool: Optional tool name.
            scope: Rate limit scope.

        Returns:
            Rate limit key string.
        """
        scope = scope or self._config.rate_limit.scope

        if scope == RateLimitScope.GLOBAL:
            return "global"
        elif scope == RateLimitScope.PER_SERVER:
            return f"server:{server}"
        else:  # PER_TOOL
            return f"server:{server}:tool:{tool}"

    async def _get_rate_limit_bucket(
        self,
        server: str,
        tool: Optional[str] = None,
    ) -> RateLimitBucket:
        """Get or create a rate limit bucket.

        Args:
            server: Server name.
            tool: Optional tool name.

        Returns:
            RateLimitBucket for this scope.
        """
        key = self._make_rate_limit_key(server, tool)
        scope = self._config.rate_limit.scope

        async with self._lock:
            if key not in self._rate_limits:
                self._rate_limits[key] = RateLimitBucket(
                    tokens=float(self._config.rate_limit.max_requests),
                    last_refill=datetime.now(),
                    config=self._config.rate_limit,
                )
            return self._rate_limits[key]

    async def _wait_for_rate_limit(
        self,
        server: str,
        tool: Optional[str] = None,
    ) -> None:
        """Wait until rate limit allows the request.

        Args:
            server: Server name.
            tool: Optional tool name.
        """
        bucket = await self._get_rate_limit_bucket(server, tool)

        if bucket.consume():
            return

        wait_time = bucket.wait_time
        logger.debug(
            f"Rate limit hit for '{server}', waiting {wait_time:.2f}s"
        )
        await asyncio.sleep(wait_time)

    def _get_from_cache(
        self,
        server: str,
        tool: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Get a cached result if available and not expired.

        Args:
            server: Server name.
            tool: Tool name.
            arguments: Tool arguments.

        Returns:
            Cached result or None.
        """
        if not self._config.cache_enabled:
            return None

        key = self._make_cache_key(server, tool, arguments)

        if key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired:
                entry.hit_count += 1
                logger.debug(
                    f"Cache hit for '{server}.{tool}' (hit #{entry.hit_count})"
                )
                return entry.value
            else:
                del self._cache[key]

        return None

    def _add_to_cache(
        self,
        server: str,
        tool: str,
        arguments: Optional[dict[str, Any]],
        value: Any,
    ) -> None:
        """Add a result to the cache.

        Args:
            server: Server name.
            tool: Tool name.
            arguments: Tool arguments.
            value: Result to cache.
        """
        if not self._config.cache_enabled:
            return

        key = self._make_cache_key(server, tool, arguments)
        now = datetime.now()

        # Evict oldest if at capacity
        if len(self._cache) >= self._config.cache_max_entries:
            oldest_key = min(
                self._cache.keys(),
                key=lambda k: self._cache[k].created_at,
            )
            del self._cache[oldest_key]

        self._cache[key] = CacheEntry(
            value=value,
            created_at=now,
            expires_at=now + timedelta(seconds=self._config.cache_ttl),
        )
        logger.debug(f"Cached result for '{server}.{tool}'")

    async def call(
        self,
        server: str,
        tool: str,
        arguments: Optional[dict[str, Any]] = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Call an MCP tool with caching and rate limiting.

        Args:
            server: Server name.
            tool: Tool name.
            arguments: Tool arguments.
            refresh: If True, bypass cache and fetch fresh result.

        Returns:
            Tool response dict with "result" or "error" key.
        """
        # Check cache unless refreshing
        if not refresh:
            cached = self._get_from_cache(server, tool, arguments)
            if cached is not None:
                return cached

        # Wait for rate limit
        await self._wait_for_rate_limit(server, tool)

        # Check for in-flight request coalescing
        cache_key = self._make_cache_key(server, tool, arguments)
        async with self._lock:
            if cache_key in self._in_flight:
                # Wait for existing request
                task = self._in_flight[cache_key]
            else:
                # Create new task
                task = asyncio.create_task(
                    self._do_call_with_retry(server, tool, arguments)
                )
                self._in_flight[cache_key] = task

        try:
            result = await asyncio.wait_for(task, timeout=self._config.timeout)
        except asyncio.TimeoutError:
            result = {"error": f"Tool call timed out after {self._config.timeout}s"}
        finally:
            async with self._lock:
                if cache_key in self._in_flight:
                    del self._in_flight[cache_key]

        # Cache successful result
        if "error" not in result:
            self._add_to_cache(server, tool, arguments, result)

        return result

    async def _do_call_with_retry(
        self,
        server: str,
        tool: str,
        arguments: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute a tool call with retry logic.

        Args:
            server: Server name.
            tool: Tool name.
            arguments: Tool arguments.

        Returns:
            Tool response dict.
        """
        last_error: Optional[str] = None

        for attempt in range(self._config.max_retries):
            try:
                result = await self._manager.call_tool(server, tool, arguments)

                if "error" not in result:
                    return result

                last_error = result.get("error")

                # Don't retry on certain errors
                if "not found" in last_error.lower() or "invalid" in last_error.lower():
                    break

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Tool call attempt {attempt + 1} failed for '{server}.{tool}': {e}"
                )

            # Exponential backoff before retry
            if attempt < self._config.max_retries - 1:
                delay = self._config.retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)

        return {"error": last_error or "Unknown error after retries"}

    def tool_to_action(
        self,
        server: str,
        tool: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert an MCP tool result to a Nexus action.

        This transforms raw MCP tool results into structured actions
        that can be used by other Nexus components.

        Args:
            server: Server name.
            tool: Tool name that produced the result.
            result: Raw tool result dict.

        Returns:
            Structured action dict with type, payload, and metadata.
        """
        if "error" in result:
            return {
                "type": "mcp_error",
                "payload": {
                    "server": server,
                    "tool": tool,
                    "error": result["error"],
                },
                "metadata": {
                    "timestamp": datetime.now().isoformat(),
                    "success": False,
                },
            }

        # Determine action type based on tool name patterns
        action_type = self._infer_action_type(tool)

        return {
            "type": action_type,
            "payload": {
                "server": server,
                "tool": tool,
                "data": result.get("result"),
            },
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "success": True,
                "server": server,
                "tool": tool,
            },
        }

    def _infer_action_type(self, tool: str) -> str:
        """Infer the Nexus action type from tool name.

        Args:
            tool: Tool name (e.g., "list_issues", "create_pr").

        Returns:
            Inferred action type string.
        """
        tool_lower = tool.lower()

        # GitHub-style patterns
        if "create" in tool_lower and "issue" in tool_lower:
            return "github_create_issue"
        if "list" in tool_lower and "issue" in tool_lower:
            return "github_list_issues"
        if "create" in tool_lower and "pull" in tool_lower:
            return "github_create_pr"
        if "list" in tool_lower and "pr" in tool_lower:
            return "github_list_prs"
        if "get" in tool_lower and "user" in tool_lower:
            return "github_get_user"

        # Filesystem patterns
        if "read" in tool_lower and "file" in tool_lower:
            return "fs_read_file"
        if "write" in tool_lower and "file" in tool_lower:
            return "fs_write_file"
        if "list" in tool_lower and "directory" in tool_lower:
            return "fs_list_directory"

        # Database patterns
        if "query" in tool_lower:
            return "db_query"
        if "execute" in tool_lower:
            return "db_execute"

        # Generic fallback
        return f"mcp_tool_{tool}"

    def invalidate_cache(
        self,
        server: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> int:
        """Invalidate cached entries.

        Args:
            server: If provided, only invalidate for this server.
            tool: If provided, only invalidate for this tool.

        Returns:
            Number of entries invalidated.
        """
        if not self._config.cache_enabled:
            return 0

        count = 0
        keys_to_delete = []

        for key, entry in self._cache.items():
            # Parse key to check server/tool
            # Key format: sha256("server:tool:args")
            # We can't easily parse it, so check if tool matches

            if server is not None:
                # Would need to store metadata to filter properly
                # For now, clear all if server specified
                pass

        if server is None and tool is None:
            count = len(self._cache)
            self._cache.clear()
        elif server is not None:
            # Clear all entries when server is specified (metadata not stored)
            for key in list(self._cache.keys()):
                keys_to_delete.append(key)

            for key in keys_to_delete:
                del self._cache[key]
                count += 1

        logger.info(f"Invalidated {count} cache entries")
        return count

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with cache stats (size, hits, etc.).
        """
        total_hits = sum(e.hit_count for e in self._cache.values())
        total_entries = len(self._cache)

        return {
            "enabled": self._config.cache_enabled,
            "entries": total_entries,
            "max_entries": self._config.cache_max_entries,
            "ttl_seconds": self._config.cache_ttl,
            "total_hits": total_hits,
        }

    def get_rate_limit_status(
        self,
        server: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get rate limit status.

        Args:
            server: Optional server to check.

        Returns:
            Dict with rate limit status.
        """
        status = {
            "config": {
                "max_requests": self._config.rate_limit.max_requests,
                "window_seconds": self._config.rate_limit.window_seconds,
                "scope": self._config.rate_limit.scope.name,
            },
            "buckets": {},
        }

        for key, bucket in self._rate_limits.items():
            status["buckets"][key] = {
                "available_tokens": round(bucket.tokens, 2),
                "max_tokens": bucket.config.max_requests,
                "wait_time_seconds": round(bucket.wait_time, 2),
            }

        return status
