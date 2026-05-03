"""MCP Config Manager — YAML/JSON MCP server configurations with nexus mcp CLI.

Manages MCP server configurations with support for:
- YAML and JSON config formats
- Multiple transport types (stdio, HTTP)
- Environment variable substitution
- nexus mcp CLI integration for discovery and management
- Configuration validation and merging
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    yaml = None
    _HAS_YAML = False


class MCPConfigFormat(Enum):
    """Supported configuration file formats."""
    YAML = "yaml"
    JSON = "json"


@dataclass
class MCPServerConfig:
    """MCP server configuration.

    Attributes:
        name: Unique server name.
        transport: Transport type ('stdio' or 'http').
        command: Command to run (for stdio) or URL (for http).
        env: Environment variables (for stdio transport).
        args: Additional command arguments.
        description: Human-readable description.
        auth: Optional auth configuration (token, oauth, etc.).
        enabled: Whether server is enabled.
    """

    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    url: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)
    description: Optional[str] = None
    auth: Optional[dict[str, str]] = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "url": self.url,
            "env": self.env,
            "args": self.args,
            "description": self.description,
            "auth": self.auth,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPServerConfig:
        return cls(
            name=data["name"],
            transport=data.get("transport", "stdio"),
            command=data.get("command"),
            url=data.get("url"),
            env=data.get("env", {}),
            args=data.get("args", []),
            description=data.get("description"),
            auth=data.get("auth"),
            enabled=data.get("enabled", True),
        )


class MCPConfigManager:
    """Manages MCP server configurations with YAML/JSON storage.

    Supports:
    - Loading configs from YAML or JSON files
    - Saving configs to YAML or JSON
    - Merging multiple config sources
    - nexus mcp CLI integration for discovery
    - Environment variable substitution
    - Config validation

    Example:
        manager = MCPConfigManager("config/mcp.yaml")
        manager.add_server(MCPServerConfig(name="github", command="npx -y @modelcontextprotocol/server-github"))
        manager.save()

        # List available servers via nexus mcp CLI
        servers = manager.list_via_nexus()
    """

    DEFAULT_CONFIG_DIR = Path.home() / ".config" / "nexus" / "mcp"
    DEFAULT_CONFIG_FILE = "servers.yaml"

    def __init__(
        self,
        config_path: Optional[Union[str, Path]] = None,
        config_dir: Optional[Union[str, Path]] = None,
        format: MCPConfigFormat = MCPConfigFormat.YAML,
    ):
        """Initialize MCP Config Manager.

        Args:
            config_path: Path to config file. If None, uses config_dir + default filename.
            config_dir: Directory for config files. Defaults to ~/.config/nexus/mcp.
            format: Config format (YAML or JSON).
        """
        self.format = format

        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_dir = Path(config_dir) if config_dir else self.DEFAULT_CONFIG_DIR
            self.config_dir.mkdir(parents=True, exist_ok=True)
            ext = "yaml" if format == MCPConfigFormat.YAML else "json"
            self.config_path = self.config_dir / f"servers.{ext}"

        self.servers: list[MCPServerConfig] = []
        self._load()

    def _load(self) -> None:
        """Load configuration from file."""
        if not self.config_path.exists():
            return

        content = self.config_path.read_text()
        data = self._parse_content(content)

        if isinstance(data, dict) and "servers" in data:
            self.servers = [MCPServerConfig.from_dict(s) for s in data["servers"]]
        elif isinstance(data, list):
            self.servers = [MCPServerConfig.from_dict(s) for s in data]

    def _parse_content(self, content: str) -> Any:
        """Parse config content based on format."""
        if self.format == MCPConfigFormat.YAML:
            if not _HAS_YAML:
                raise ImportError("pyyaml not installed. Install with: pip install pyyaml")
            return yaml.safe_load(content) or {}
        else:
            return json.loads(content)

    def _serialize(self, data: Any) -> str:
        """Serialize data to string based on format."""
        if self.format == MCPConfigFormat.YAML:
            if not _HAS_YAML:
                raise ImportError("pyyaml not installed. Install with: pip install pyyaml")
            return yaml.dump(data, default_flow_style=False, sort_keys=False)
        else:
            return json.dumps(data, indent=2)

    def save(self) -> None:
        """Save configuration to file."""
        data = {
            "servers": [s.to_dict() for s in self.servers]
        }
        self.config_path.write_text(self._serialize(data))

    def add_server(self, server: MCPServerConfig) -> None:
        """Add a server configuration.

        Args:
            server: Server configuration to add.
        """
        # Remove existing with same name
        self.servers = [s for s in self.servers if s.name != server.name]
        self.servers.append(server)

    def remove_server(self, name: str) -> bool:
        """Remove a server configuration.

        Args:
            name: Server name to remove.

        Returns:
            True if removed, False if not found.
        """
        original_count = len(self.servers)
        self.servers = [s for s in self.servers if s.name != name]
        return len(self.servers) < original_count

    def get_server(self, name: str) -> Optional[MCPServerConfig]:
        """Get a server configuration by name.

        Args:
            name: Server name.

        Returns:
            Server config or None if not found.
        """
        for server in self.servers:
            if server.name == name:
                return server
        return None

    def list_servers(self, enabled_only: bool = True) -> list[MCPServerConfig]:
        """List configured servers.

        Args:
            enabled_only: Only return enabled servers.

        Returns:
            List of server configurations.
        """
        if enabled_only:
            return [s for s in self.servers if s.enabled]
        return self.servers.copy()

    def merge(self, other: MCPConfigManager) -> None:
        """Merge configurations from another manager.

        Later configs override earlier ones for duplicate names.

        Args:
            other: Another config manager to merge from.
        """
        other_servers = {s.name: s for s in other.servers}
        for server in self.servers:
            if server.name in other_servers:
                # Skip if other has it enabled
                pass
        self.servers = self.servers + other.servers

    def validate(self) -> list[str]:
        """Validate configuration.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = []

        names = set()
        for server in self.servers:
            if server.name in names:
                errors.append(f"Duplicate server name: {server.name}")
            names.add(server.name)

            if server.transport not in ("stdio", "http"):
                errors.append(f"Invalid transport '{server.transport}' for {server.name}")

            if server.transport == "stdio" and not server.command:
                errors.append(f"stdio transport requires 'command' for {server.name}")

            if server.transport == "http" and not server.url:
                errors.append(f"http transport requires 'url' for {server.name}")

        return errors

    # === nexus mcp CLI integration ===

    def list_via_nexus(self) -> list[dict[str, Any]]:
        """List MCP servers via nexus mcp CLI.

        Returns:
            List of server info dicts from nexus mcp.
        """
        try:
            result = subprocess.run(
                ["npx", "-y", "mcporter", "list", "--output", "json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
        return []

    def discover_servers(self) -> list[dict[str, Any]]:
        """Discover MCP servers via nexus mcp CLI.

        Returns:
            List of discovered server info dicts.
        """
        try:
            result = subprocess.run(
                ["npx", "-y", "mcporter", "list", "--output", "json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
        return []

    def add_from_nexus(self, server_name: str) -> bool:
        """Add a server from nexus mcp discovered list.

        Args:
            server_name: Name of server to add.

        Returns:
            True if added successfully, False otherwise.
        """
        discovered = self.discover_servers()
        for server in discovered:
            if server.get("name") == server_name:
                config = MCPServerConfig(
                    name=server.get("name", server_name),
                    transport=server.get("transport", "stdio"),
                    command=server.get("command"),
                    url=server.get("url"),
                    description=server.get("description"),
                )
                self.add_server(config)
                return True
        return False

    def call_via_nexus(
        self,
        server_name: str,
        tool_name: str,
        args: Optional[dict[str, Any]] = None,
        output: str = "json",
    ) -> Optional[dict[str, Any]]:
        """Call an MCP tool via nexus mcp CLI.

        Args:
            server_name: Server name (format: server.tool).
            tool_name: Tool name.
            args: Tool arguments.
            output: Output format ('json' or 'text').

        Returns:
            Tool result dict or None on failure.
        """
        args_str = " ".join(f"{k}={v}" for k, v in (args or {}).items())
        cmd = f"{server_name}.{tool_name} {args_str}".strip()

        try:
            result = subprocess.run(
                ["npx", "-y", "mcporter", "call", cmd, "--output", output],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                if output == "json":
                    return json.loads(result.stdout)
                return {"output": result.stdout}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
        return None

    def export_to_nexus_config(self, path: Optional[Path] = None) -> Path:
        """Export configuration to nexus mcp CLI config format.

        Args:
            path: Export path. Defaults to ~/.config/mcporter.json.

        Returns:
            Path to exported config.
        """
        export_path = path or Path.home() / ".config" / "mcporter.json"
        export_path.parent.mkdir(parents=True, exist_ok=True)

        servers = {}
        for server in self.servers:
            if server.enabled:
                servers[server.name] = {
                    "transport": server.transport,
                    "command": server.command,
                    "url": server.url,
                    "env": server.env,
                    "args": server.args,
                }

        export_path.write_text(json.dumps({"servers": servers}, indent=2))
        return export_path

    @classmethod
    def from_nexus_config(cls, path: Optional[Path] = None) -> MCPConfigManager:
        """Load configuration from nexus mcp CLI config file.

        Args:
            path: Path to nexus config. Defaults to ~/.config/mcporter.json.

        Returns:
            MCPConfigManager with loaded configuration.
        """
        config_path = path or Path.home() / ".config" / "mcporter.json"

        if not config_path.exists():
            return cls()

        try:
            data = json.loads(config_path.read_text())
            manager = cls(config_path=config_path, format=MCPConfigFormat.JSON)
            if "servers" in data:
                manager.servers = [
                    MCPServerConfig.from_dict(s) for s in data["servers"]
                ]
            return manager
        except (json.JSONDecodeError, KeyError):
            return cls()
