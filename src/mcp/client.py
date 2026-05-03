"""MCP Client — True MCP client that connects to MCP servers via stdio.

Provides a complete MCP client implementation that:
- Spawns MCP servers as subprocesses using asyncio
- Communicates via JSON-RPC 2.0 over stdin/stdout
- Parses server capabilities (tools, resources, prompts)
- Handles both notification and response messages
- Auto-reconnects on failure with exponential backoff
- Supports configurable timeouts for tool calls

Usage:
    from .config import MCPServerConfig
    config = MCPServerConfig(name="github", command="npx", args=["-y", "@modelcontextprotocol/server-github"])
    client = MCPClient(config)
    await client.connect()
    tools = await client.list_tools()
    result = await client.call_tool("tool_name", {"arg": "value"})
    await client.disconnect()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Default timeout for tool calls in seconds
DEFAULT_TOOL_TIMEOUT = 60

# Exponential backoff delays for reconnection
RECONNECT_DELAYS = (1, 2, 4, 8, 16, 32)


@dataclass
class ToolDefinition:
    """MCP tool definition.
    
    Attributes:
        name: Unique tool name.
        description: Human-readable description.
        input_schema: JSON schema for tool input.
    """
    name: str
    description: Optional[str] = None
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResourceDefinition:
    """MCP resource definition.
    
    Attributes:
        uri: Resource URI.
        name: Human-readable name.
        description: Resource description.
        mime_type: Optional MIME type.
    """
    uri: str
    name: Optional[str] = None
    description: Optional[str] = None
    mime_type: Optional[str] = None


@dataclass
class PromptDefinition:
    """MCP prompt definition.
    
    Attributes:
        name: Prompt name.
        description: Human-readable description.
        arguments: List of argument definitions.
    """
    name: str
    description: Optional[str] = None
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ServerCapabilities:
    """MCP server capabilities.
    
    Attributes:
        tools: Whether server supports tools.
        resources: Whether server supports resources.
        prompts: Whether server supports prompts.
    """
    tools: bool = False
    resources: bool = False
    prompts: bool = False


class MCPClient:
    """True MCP client that connects to MCP servers via stdio.
    
    Implements the MCP protocol over stdio transport:
    - Spawns the MCP server as a subprocess
    - Sends JSON-RPC 2.0 messages over stdin/stdout
    - Handles both request/response and notification messages
    - Auto-reconnects on connection failure
    - Supports configurable timeouts
    
    Attributes:
        config: MCP server configuration.
        timeout: Default timeout for tool calls in seconds.
    """
    
    def __init__(self, config: "MCPConfig", timeout: int = DEFAULT_TOOL_TIMEOUT) -> None:
        """Initialize MCP client.
        
        Args:
            config: MCP server configuration with command, args, env.
            timeout: Default timeout for tool calls in seconds.
        """
        self.config = config
        self.timeout = timeout
        
        # Subprocess handles
        self._process: Optional[asyncio.subprocess.Process] = None
        self._stdin_writer: Optional[asyncio.StreamWriter] = None
        self._stdout_reader: Optional[asyncio.StreamReader] = None
        
        # Message handling
        self._request_id = 0
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._notification_handlers: dict[str, callable] = {}
        
        # Server state
        self._connected = False
        self._capabilities = ServerCapabilities()
        self._tools: list[ToolDefinition] = []
        self._resources: list[ResourceDefinition] = []
        self._prompts: list[PromptDefinition] = []
        
        # Reconnection state
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        
        # Background tasks
        self._read_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected to server."""
        return self._connected
    
    @property
    def capabilities(self) -> ServerCapabilities:
        """Get server capabilities."""
        return self._capabilities
    
    @property
    def tools(self) -> list[ToolDefinition]:
        """Get list of available tools."""
        return self._tools
    
    @property
    def resources(self) -> list[ResourceDefinition]:
        """Get list of available resources."""
        return self._resources
    
    @property
    def prompts(self) -> list[PromptDefinition]:
        """Get list of available prompts."""
        return self._prompts
    
    async def connect(self) -> None:
        """Connect to the MCP server.
        
        Spawns the server subprocess and initializes the connection.
        Performs handshake and capability negotiation.
        
        Raises:
            ConnectionError: If connection fails.
            RuntimeError: If already connected.
        """
        async with self._lock:
            if self._connected:
                raise RuntimeError("Already connected to server")
            
            try:
                await self._spawn_process()
                await self._initialize()
                self._connected = True
                self._reconnect_attempts = 0
                logger.info(f"Connected to MCP server: {self.config.name}")
            except Exception as e:
                await self._cleanup()
                raise ConnectionError(f"Failed to connect: {e}") from e
    
    async def disconnect(self) -> None:
        """Disconnect from the MCP server.
        
        Gracefully shuts down the connection and terminates the subprocess.
        """
        async with self._lock:
            await self._cleanup()
            logger.info(f"Disconnected from MCP server: {self.config.name}")
    
    async def _spawn_process(self) -> None:
        """Spawn the MCP server subprocess."""
        # Build command
        cmd = self.config.command
        args = self.config.args or []
        
        # Prepare environment
        env = self._prepare_environment()
        
        # Spawn subprocess with stdio
        self._process = await asyncio.create_subprocess_exec(
            cmd,
            *args,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        self._stdin_writer = self._process.stdin
        self._stdout_reader = self._process.stdout
        
        # Start reading responses in background
        self._read_task = asyncio.create_task(self._read_loop())
    
    def _prepare_environment(self) -> dict[str, str]:
        """Prepare environment for subprocess.
        
        Filters environment variables for security and adds configured ones.
        
        Returns:
            Filtered environment dict.
        """
        # Start with safe baseline
        safe_vars = {
            "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"
        }
        
        env = {k: os.environ[k] for k in safe_vars if k in os.environ}
        
        # Add XDG variables
        for key in os.environ:
            if key.startswith("XDG_"):
                env[key] = os.environ[key]
        
        # Add configured environment
        if self.config.env:
            env.update(self.config.env)
        
        return env
    
    async def _initialize(self) -> None:
        """Send initialize request and process response.
        
        Performs the MCP protocol handshake.
        """
        # Send initialize request
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
            },
            "clientInfo": {
                "name": "nexus-mcp-client",
                "version": "1.0.0",
            },
        }
        
        response = await self._send_request("initialize", init_params)
        
        # Process response
        if "result" in response:
            result = response["result"]
            server_info = result.get("serverInfo", {})
            capabilities = result.get("capabilities", {})
            
            # Parse capabilities
            self._capabilities = ServerCapabilities(
                tools=bool(capabilities.get("tools")),
                resources=bool(capabilities.get("resources")),
                prompts=bool(capabilities.get("prompts")),
            )
            
            logger.debug(f"Server capabilities: tools={self._capabilities.tools}, "
                        f"resources={self._capabilities.resources}, "
                        f"prompts={self._capabilities.prompts}")
        
        # Send initialized notification (required by MCP protocol)
        await self._send_notification("initialized", {})
        
        # Fetch available tools/resources/prompts if supported
        if self._capabilities.tools:
            await self._fetch_tools()
        if self._capabilities.resources:
            await self._fetch_resources()
        if self._capabilities.prompts:
            await self._fetch_prompts()
    
    async def _fetch_tools(self) -> None:
        """Fetch and parse available tools."""
        try:
            response = await self._send_request("tools/list", {})
            if "result" in response:
                tools_data = response["result"].get("tools", [])
                self._tools = [
                    ToolDefinition(
                        name=t.get("name"),
                        description=t.get("description"),
                        input_schema=t.get("inputSchema", {}),
                    )
                    for t in tools_data
                ]
                logger.debug(f"Fetched {len(self._tools)} tools")
        except Exception as e:
            logger.warning(f"Failed to fetch tools: {e}")
    
    async def _fetch_resources(self) -> None:
        """Fetch and parse available resources."""
        try:
            response = await self._send_request("resources/list", {})
            if "result" in response:
                resources_data = response["result"].get("resources", [])
                self._resources = [
                    ResourceDefinition(
                        uri=r.get("uri"),
                        name=r.get("name"),
                        description=r.get("description"),
                        mime_type=r.get("mimeType"),
                    )
                    for r in resources_data
                ]
                logger.debug(f"Fetched {len(self._resources)} resources")
        except Exception as e:
            logger.warning(f"Failed to fetch resources: {e}")
    
    async def _fetch_prompts(self) -> None:
        """Fetch and parse available prompts."""
        try:
            response = await self._send_request("prompts/list", {})
            if "result" in response:
                prompts_data = response["result"].get("prompts", [])
                self._prompts = [
                    PromptDefinition(
                        name=p.get("name"),
                        description=p.get("description"),
                        arguments=p.get("arguments", []),
                    )
                    for p in prompts_data
                ]
                logger.debug(f"Fetched {len(self._prompts)} prompts")
        except Exception as e:
            logger.warning(f"Failed to fetch prompts: {e}")
    
    async def _read_loop(self) -> None:
        """Background loop to read and handle server messages.
        
        Handles both JSON-RPC responses and notifications.
        """
        try:
            while self._connected and self._stdout_reader:
                try:
                    line = await self._stdout_reader.readline()
                    if not line:
                        # EOF - server exited
                        logger.warning("Server process ended")
                        break
                    
                    message = json.loads(line.decode("utf-8"))
                    await self._handle_message(message)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON message: {e}")
                except Exception as e:
                    logger.error(f"Error in read loop: {e}")
                    break
                    
        except asyncio.CancelledError:
            pass
        finally:
            if self._connected:
                await self._handle_disconnect()
    
    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle incoming JSON-RPC message.
        
        Args:
            message: Parsed JSON-RPC message.
        """
        # Check if it's a response to a pending request
        if "id" in message:
            request_id = str(message["id"])
            
            if request_id in self._pending_requests:
                future = self._pending_requests.pop(request_id)
                
                if "error" in message:
                    future.set_exception(Exception(message["error"].get("message", "Unknown error")))
                else:
                    future.set_result(message)
            else:
                logger.warning(f"Received response with unknown id: {request_id}")
        
        # Handle notification messages
        elif "method" in message and "id" not in message:
            method = message["method"]
            params = message.get("params", {})
            
            handler = self._notification_handlers.get(method)
            if handler:
                try:
                    await handler(params)
                except Exception as e:
                    logger.error(f"Notification handler error for {method}: {e}")
            else:
                logger.debug(f"Received unhandled notification: {method}")
    
    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response.
        
        Args:
            method: RPC method name.
            params: Method parameters.
            
        Returns:
            Response dict with "result" or "error".
            
        Raises:
            TimeoutError: If request times out.
        """
        request_id = str(self._request_id)
        self._request_id += 1
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        
        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        try:
            # Send request
            await self._send_json(request)
            
            # Wait for response with timeout
            return await asyncio.wait_for(future, timeout=self.timeout)
            
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError(f"Request {method} timed out after {self.timeout}s")
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            raise
    
    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected).
        
        Args:
            method: RPC method name.
            params: Method parameters.
        """
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        
        try:
            await self._send_json(notification)
        except Exception as e:
            logger.error(f"Failed to send notification {method}: {e}")
    
    async def _send_json(self, data: dict[str, Any]) -> None:
        """Send JSON-RPC message to server.
        
        Args:
            data: JSON-RPC message dict.
            
        Raises:
            ConnectionError: If not connected.
        """
        if not self._stdin_writer or self._connected is False:
            raise ConnectionError("Not connected to server")
        
        try:
            message = json.dumps(data) + "\n"
            self._stdin_writer.write(message.encode("utf-8"))
            await self._stdin_writer.drain()
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise ConnectionError(f"Failed to send message: {e}") from e
    
    async def list_tools(self) -> list[ToolDefinition]:
        """List available tools from the server.
        
        Returns:
            List of ToolDefinition objects.
            
        Raises:
            RuntimeError: If not connected or tools not supported.
        """
        if not self._connected:
            raise RuntimeError("Not connected to server")
        
        if not self._capabilities.tools:
            return []
        
        # Refresh tools from server
        await self._fetch_tools()
        return self._tools
    
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the MCP server.
        
        Args:
            name: Tool name.
            arguments: Tool arguments as key-value pairs.
            
        Returns:
            Tool result dict with "content" or "error".
            
        Raises:
            RuntimeError: If not connected or tools not supported.
            TimeoutError: If tool call times out.
        """
        if not self._connected:
            raise RuntimeError("Not connected to server")
        
        if not self._capabilities.tools:
            raise RuntimeError("Server does not support tools")
        
        try:
            params = {
                "name": name,
                "arguments": arguments,
            }
            
            response = await self._send_request("tools/call", params)
            
            if "error" in response:
                return {"error": response["error"]}
            
            return response.get("result", {})
            
        except TimeoutError:
            raise
        except Exception as e:
            logger.error(f"Tool call failed for {name}: {e}")
            return {"error": str(e)}
    
    async def ping(self) -> bool:
        """Ping the MCP server to check connectivity.
        
        Sends a ping and waits for response.
        
        Returns:
            True if server responds, False otherwise.
        """
        if not self._connected:
            return False
        
        try:
            # Send ping (using sampling for health check)
            response = await self._send_request("ping", {})
            return "result" in response
        except Exception as e:
            logger.warning(f"Ping failed: {e}")
            return False
    
    def register_notification_handler(self, method: str, handler: callable) -> None:
        """Register a handler for incoming notifications.
        
        Args:
            method: Notification method name.
            handler: Async callable to handle the notification.
        """
        self._notification_handlers[method] = handler
    
    async def _handle_disconnect(self) -> None:
        """Handle unexpected disconnect with auto-reconnect."""
        self._connected = False
        
        if self._reconnect_attempts < self._max_reconnect_attempts:
            delay = RECONNECT_DELAYS[min(self._reconnect_attempts, len(RECONNECT_DELAYS) - 1)]
            self._reconnect_attempts += 1
            
            logger.info(f"Attempting reconnect in {delay}s (attempt {self._reconnect_attempts})")
            
            await asyncio.sleep(delay)
            
            try:
                await self.connect()
            except Exception as e:
                logger.error(f"Reconnect failed: {e}")
        else:
            logger.error(f"Max reconnect attempts ({self._max_reconnect_attempts}) exceeded")
    
    async def _cleanup(self) -> None:
        """Clean up resources on disconnect."""
        self._connected = False
        
        # Cancel read task
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        
        # Close stdin to signal EOF
        if self._stdin_writer:
            try:
                self._stdin_writer.close()
                await self._stdin_writer.wait_closed()
            except Exception:
                pass
            self._stdin_writer = None
        
        # Terminate process
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception:
                pass
            self._process = None
        
        self._stdout_reader = None
        
        # Clear pending requests
        for future in self._pending_requests.values():
            future.cancel()
        self._pending_requests.clear()
    
    async def __aenter__(self) -> "MCPClient":
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()


# Import MCPConfig for use with this client
# Note: MCPConfig refers to MCPServerConfig from ..mcp.config
try:
    from .config import MCPServerConfig as MCPConfig
except ImportError:
    # Fallback if MCPServerConfig not defined in config module
    # Will be available when config module is properly set up
    MCPConfig = None  # type: ignore
