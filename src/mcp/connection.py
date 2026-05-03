"""MCP Connection Manager.

Provides async MCP server lifecycle management: connect, disconnect, health monitoring,
and automatic reconnection with exponential backoff. Designed for long-running MCP
server connections in the Nexus multi-agent system.

Usage:
    manager = MCPConnectionManager()
    await manager.connect("github", server_config)
    await manager.call_tool("github", "list_issues", {"limit": 5})
    await manager.disconnect("github")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MCPConnectionState(Enum):
    """MCP server connection states."""

    DISCONNECTED = auto()
    """Server is not connected."""

    CONNECTING = auto()
    """Connection in progress."""

    CONNECTED = auto()
    """Active and ready to handle requests."""

    HEALTHY = auto()
    """Connected and passing health checks."""

    DEGRADED = auto()
    """Connected but showing intermittent issues."""

    RECONNECTING = auto()
    """Attempting automatic reconnection."""

    FAILED = auto()
    """Connection failed permanently (after max retries)."""

    CLOSING = auto()
    """Graceful shutdown in progress."""


@dataclass
class MCPServerInfo:
    """Metadata about a connected MCP server.

    Attributes:
        name: Unique identifier for the server.
        state: Current connection state.
        transport: Transport type ("stdio" or "http").
        endpoint: Server endpoint (command for stdio, URL for http).
        connected_at: Timestamp when connection was established.
        last_health_check: Timestamp of last successful health check.
        last_error: Most recent error message, if any.
        retry_count: Number of reconnection attempts made.
        max_retries: Maximum allowed reconnection attempts.
        tools: List of tool names exposed by this server.
    """

    name: str
    state: MCPConnectionState = MCPConnectionState.DISCONNECTED
    transport: Optional[str] = None
    endpoint: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_health_check: Optional[datetime] = None
    last_error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 5
    tools: list[str] = field(default_factory=list)

    @property
    def is_connected(self) -> bool:
        """Check if server has an active connection."""
        return self.state in (
            MCPConnectionState.CONNECTED,
            MCPConnectionState.HEALTHY,
            MCPConnectionState.DEGRADED,
        )

    @property
    def can_retry(self) -> bool:
        """Check if reconnection can be attempted."""
        return self.retry_count < self.max_retries


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection.

    Attributes:
        name: Unique identifier for the server.
        command: Executable to run (stdio transport).
        args: Arguments passed to the command.
        env: Environment variables for the subprocess.
        url: Server URL (http transport).
        headers: HTTP headers for http transport.
        timeout: Per-tool-call timeout in seconds.
        connect_timeout: Initial connection timeout in seconds.
        health_check_interval: Seconds between health checks.
        max_retries: Maximum reconnection attempts.
    """

    name: str
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: int = 120
    connect_timeout: int = 60
    health_check_interval: int = 30
    max_retries: int = 5


class MCPConnectionManager:
    """Async MCP server lifecycle manager.

    Manages connections to MCP servers with automatic reconnection,
    health monitoring, and graceful shutdown.

    Features:
        - Async connect/disconnect with timeout handling
        - Automatic reconnection with exponential backoff
        - Periodic health checks
        - Per-server state tracking
        - Thread-safe operations

    Usage:
        manager = MCPConnectionManager()

        # Connect to a server
        await manager.connect("github", MCPServerConfig(
            name="github",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
        ))

        # Check connection state
        info = manager.get_server_info("github")
        print(f"Connected: {info.is_connected}")

        # Call a tool
        result = await manager.call_tool("github", "list_issues", {"limit": 5})

        # Disconnect
        await manager.disconnect("github")
    """

    # Exponential backoff delays in seconds
    BACKOFF_DELAYS = (1, 2, 4, 8, 16, 32, 64)
    MAX_BACKOFF_SECONDS = 60

    def __init__(self):
        """Initialize the connection manager."""
        self._servers: dict[str, MCPServerInfo] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._health_tasks: dict[str, asyncio.Task] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._mcp_client: Any = None  # Lazy-loaded when mcp package available

    async def _ensure_mcp_client(self) -> Any:
        """Lazily import and return the MCP client."""
        if self._mcp_client is None:
            try:
                from ..mcp.client import Client
                self._mcp_client = Client
            except ImportError:
                raise ImportError(
                    "mcp package not installed. Install with: pip install mcp"
                )
        return self._mcp_client

    async def connect(self, name: str, config: MCPServerConfig) -> MCPServerInfo:
        """Connect to an MCP server.

        Args:
            name: Unique identifier for the server.
            config: Server connection configuration.

        Returns:
            MCPServerInfo with connection details.

        Raises:
            ConnectionError: If connection fails.
            ValueError: If config is invalid.
        """
        async with self._lock:
            if name in self._servers and self._servers[name].is_connected:
                logger.warning(f"Server '{name}' already connected")
                return self._servers[name]

            # Initialize server info
            server_info = MCPServerInfo(
                name=name,
                state=MCPConnectionState.CONNECTING,
                max_retries=config.max_retries,
            )
            self._servers[name] = server_info
            self._configs[name] = config

            try:
                # Determine transport type
                if config.url:
                    server_info.transport = "http"
                    server_info.endpoint = config.url
                    await self._connect_http(name, config)
                elif config.command:
                    server_info.transport = "stdio"
                    server_info.endpoint = f"{config.command} {' '.join(config.args)}"
                    await self._connect_stdio(name, config)
                else:
                    raise ValueError(
                        f"Server '{name}' must have either 'url' or 'command' configured"
                    )

                server_info.state = MCPConnectionState.CONNECTED
                server_info.connected_at = datetime.now()
                server_info.retry_count = 0

                # Start health check loop
                self._start_health_check(name, config)

                logger.info(f"Connected to MCP server '{name}'")
                return server_info

            except Exception as e:
                server_info.state = MCPConnectionState.FAILED
                server_info.last_error = str(e)
                logger.error(f"Failed to connect to MCP server '{name}': {e}")
                raise ConnectionError(f"Failed to connect to '{name}': {e}") from e

    async def _connect_stdio(self, name: str, config: MCPServerConfig) -> None:
        """Connect via stdio transport."""
        client = await self._ensure_mcp_client()

        # Filter environment for security
        safe_env = self._filter_environment(config.env)

        # Build command
        cmd = [config.command, *config.args] if config.args else [config.command]

        # Create client session
        # Note: In production, this would use the actual MCP client API
        logger.debug(f"Connecting to stdio server: {' '.join(cmd)}")

        # Placeholder for actual stdio connection
        # In real implementation, this would be:
        # async with client.cli(
        #     command=config.command,
        #     args=config.args,
        #     env=safe_env,
        # ) as session:
        #     tools = await session.list_tools()
        #     self._servers[name].tools = [t.name for t in tools]

        # Simulate connection for now
        await asyncio.sleep(0.1)  # Simulated connection delay

    async def _connect_http(self, name: str, config: MCPServerConfig) -> None:
        """Connect via HTTP transport."""
        # Placeholder for actual HTTP connection
        # In real implementation, this would use:
        # async with client.streamable_http(
        #     url=config.url,
        #     headers=config.headers,
        #     timeout=config.timeout,
        # ) as session:
        #     tools = await session.list_tools()
        #     self._servers[name].tools = [t.name for t in tools]

        logger.debug(f"Connecting to HTTP server: {config.url}")
        await asyncio.sleep(0.1)  # Simulated connection delay

    def _filter_environment(self, extra_env: dict[str, str]) -> dict[str, str]:
        """Filter environment variables for security.

        Only passes safe baseline variables plus explicitly configured ones.

        Args:
            extra_env: User-specified environment variables.

        Returns:
            Filtered environment dict safe for subprocess.
        """
        # Safe baseline variables
        safe_vars = {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "TERM",
            "SHELL",
            "TMPDIR",
        }

        # Include XDG variables
        import os
        for key in os.environ:
            if key.startswith("XDG_"):
                safe_vars.add(key)

        env = {k: os.environ[k] for k in safe_vars if k in os.environ}
        env.update(extra_env)
        return env

    def _start_health_check(self, name: str, config: MCPServerConfig) -> None:
        """Start background health check loop for a server.

        Args:
            name: Server identifier.
            config: Server configuration.
        """
        if name in self._health_tasks:
            self._health_tasks[name].cancel()

        async def health_check_loop():
            while True:
                await asyncio.sleep(config.health_check_interval)
                try:
                    await self.health_check(name)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Health check failed for '{name}': {e}")

        self._health_tasks[name] = asyncio.create_task(health_check_loop())

    async def health_check(self, name: str) -> bool:
        """Perform a health check on a connected server.

        Args:
            name: Server identifier.

        Returns:
            True if server is healthy.

        Raises:
            KeyError: If server is not registered.
        """
        async with self._lock:
            if name not in self._servers:
                raise KeyError(f"Server '{name}' not found")

            server_info = self._servers[name]
            config = self._configs.get(name)

            if not server_info.is_connected:
                return False

            try:
                # In production, this would ping the server
                # e.g., await session.list_tools() as a lightweight check
                server_info.last_health_check = datetime.now()

                if server_info.state != MCPConnectionState.DEGRADED:
                    server_info.state = MCPConnectionState.HEALTHY

                return True

            except Exception as e:
                server_info.last_error = str(e)
                server_info.state = MCPConnectionState.DEGRADED
                logger.warning(f"Health check degraded for '{name}': {e}")
                return False

    async def call_tool(
        self,
        name: str,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call a tool on a connected MCP server.

        Args:
            name: Server identifier.
            tool_name: Name of the tool to call.
            arguments: Tool arguments as key-value pairs.

        Returns:
            Tool response as dict with "result" or "error" key.

        Raises:
            KeyError: If server is not registered.
            ConnectionError: If server is not connected.
        """
        async with self._lock:
            if name not in self._servers:
                raise KeyError(f"Server '{name}' not found")

            server_info = self._servers[name]

            if not server_info.is_connected:
                raise ConnectionError(
                    f"Server '{name}' is not connected (state: {server_info.state.name})"
                )

            config = self._configs[name]

        # Execute tool call (outside lock for concurrent calls)
        try:
            # In production, this would call the actual MCP tool
            # result = await session.call_tool(tool_name, arguments or {})
            result = {"result": f"Mock result from {tool_name}"}

            async with self._lock:
                if server_info.state == MCPConnectionState.DEGRADED:
                    server_info.state = MCPConnectionState.HEALTHY

            return result

        except Exception as e:
            async with self._lock:
                server_info.last_error = str(e)
                server_info.state = MCPConnectionState.DEGRADED

            logger.error(f"Tool call failed for '{name}.{tool_name}': {e}")
            return {"error": str(e)}

    async def disconnect(self, name: str) -> None:
        """Disconnect from an MCP server.

        Args:
            name: Server identifier.

        Raises:
            KeyError: If server is not registered.
        """
        async with self._lock:
            if name not in self._servers:
                raise KeyError(f"Server '{name}' not found")

            server_info = self._servers[name]
            server_info.state = MCPConnectionState.CLOSING

            # Cancel health check
            if name in self._health_tasks:
                self._health_tasks[name].cancel()
                del self._health_tasks[name]

            # Cancel reconnect task if running
            if name in self._reconnect_tasks:
                self._reconnect_tasks[name].cancel()
                del self._reconnect_tasks[name]

            # In production, close the MCP session properly
            # await session.close()

            server_info.state = MCPConnectionState.DISCONNECTED
            logger.info(f"Disconnected from MCP server '{name}'")

    async def reconnect(self, name: str) -> MCPServerInfo:
        """Attempt to reconnect to a server with exponential backoff.

        Args:
            name: Server identifier.

        Returns:
            Updated MCPServerInfo.

        Raises:
            KeyError: If server is not registered.
            ConnectionError: If max retries exceeded.
        """
        async with self._lock:
            if name not in self._servers:
                raise KeyError(f"Server '{name}' not found")

            if name in self._reconnect_tasks and not self._reconnect_tasks[name].done():
                logger.warning(f"Reconnection already in progress for '{name}'")
                return self._servers[name]

            server_info = self._servers[name]
            config = self._configs[name]

            if not server_info.can_retry:
                server_info.state = MCPConnectionState.FAILED
                raise ConnectionError(
                    f"Max retries ({server_info.max_retries}) exceeded for '{name}'"
                )

            server_info.retry_count += 1
            server_info.state = MCPConnectionState.RECONNECTING

            # Calculate backoff delay
            delay_index = min(server_info.retry_count - 1, len(self.BACKOFF_DELAYS) - 1)
            delay = self.BACKOFF_DELAYS[delay_index]

        async def _do_reconnect():
            logger.info(
                f"Reconnecting to '{name}' (attempt {server_info.retry_count}) "
                f"after {delay}s backoff"
            )
            await asyncio.sleep(delay)

            try:
                await self.connect(name, config)
            except Exception as e:
                async with self._lock:
                    if server_info.can_retry:
                        logger.warning(
                            f"Reconnection failed for '{name}': {e}. "
                            f"{server_info.max_retries - server_info.retry_count} "
                            f"retries remaining."
                        )
                    else:
                        server_info.state = MCPConnectionState.FAILED
                        logger.error(f"Max retries exceeded for '{name}': {e}")

        self._reconnect_tasks[name] = asyncio.create_task(_do_reconnect())
        return server_info

    def get_server_info(self, name: str) -> MCPServerInfo:
        """Get information about a connected server.

        Args:
            name: Server identifier.

        Returns:
            MCPServerInfo for the server.

        Raises:
            KeyError: If server is not registered.
        """
        if name not in self._servers:
            raise KeyError(f"Server '{name}' not found")
        return self._servers[name]

    def list_servers(self) -> list[MCPServerInfo]:
        """List all registered servers.

        Returns:
            List of MCPServerInfo for all servers.
        """
        return list(self._servers.values())

    async def shutdown(self) -> None:
        """Gracefully shutdown all connections.

        Cancels all health checks and reconnect tasks, then disconnects
        all servers.
        """
        logger.info("Shutting down MCP Connection Manager")

        # Cancel all health check tasks
        for name, task in self._health_tasks.items():
            task.cancel()

        # Cancel all reconnect tasks
        for name, task in self._reconnect_tasks.items():
            task.cancel()

        # Disconnect all servers
        server_names = list(self._servers.keys())
        for name in server_names:
            try:
                await self.disconnect(name)
            except Exception as e:
                logger.error(f"Error disconnecting '{name}': {e}")

        async with self._lock:
            self._servers.clear()
            self._configs.clear()
            self._health_tasks.clear()
            self._reconnect_tasks.clear()

        logger.info("MCP Connection Manager shutdown complete")
