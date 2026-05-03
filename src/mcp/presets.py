"""MCP Server Presets — GitHub, Slack, PostgreSQL configurations.

Provides ready-to-use MCP server configurations for common integrations.
Each preset includes proper auth setup, transport configuration, and
recommended tools for RalphLoop PLAN/VERIFY phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from mcp.config import MCPServerConfig


# === Base Preset ===

class MCPPreset:
    """Base class for MCP server presets."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    def get_config(self) -> MCPServerConfig:
        """Get server configuration."""
        raise NotImplementedError

    @classmethod
    def from_env(cls, **overrides) -> MCPServerConfig:
        """Create config with environment variable overrides."""
        raise NotImplementedError


# === GitHub Preset ===

GITHUB_PRESET_INFO = {
    "name": "github",
    "description": "GitHub MCP server for code, issues, PRs",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "recommended_tools": [
        "create_issue",
        "list_issues",
        "get_issue",
        "update_issue",
        "create_pull_request",
        "list_pulls",
        "get_file_contents",
        "create_file",
        "update_file",
    ],
}


class GitHubPreset(MCPPreset):
    """GitHub MCP server preset.

    Provides access to GitHub APIs for:
    - Issue management (create, list, update)
    - Pull request operations
    - Repository file operations
    - Code search

    Usage:
        config = GitHubPreset.from_env()
        manager.add_server(config)

    Environment Variables:
        GITHUB_TOKEN: GitHub personal access token (required)
        GITHUB_PERSONAL_REPO: Optional - restrict to specific repo
    """

    def __init__(
        self,
        token: Optional[str] = None,
        personal_repo: Optional[str] = None,
    ):
        super().__init__(
            name="github",
            description="GitHub MCP server for code, issues, PRs",
        )
        self.token = token
        self.personal_repo = personal_repo

    def get_config(self) -> MCPServerConfig:
        """Get GitHub MCP server configuration."""
        import os

        token = self.token or os.environ.get("GITHUB_TOKEN", "")

        env = {}
        if token:
            env["GITHUB_TOKEN"] = token
        if self.personal_repo:
            env["GITHUB_PERSONAL_REPO"] = self.personal_repo

        return MCPServerConfig(
            name="github",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env=env,
            description="GitHub MCP server for issues, PRs, and code",
        )

    @classmethod
    def from_env(cls, **overrides) -> MCPServerConfig:
        """Create GitHub preset from environment variables.

        Args:
            **overrides: Override any preset values.

        Returns:
            MCPServerConfig for GitHub MCP server.
        """
        import os

        token = overrides.get("token") or os.environ.get("GITHUB_TOKEN")
        personal_repo = overrides.get("personal_repo") or os.environ.get("GITHUB_PERSONAL_REPO")

        preset = cls(token=token, personal_repo=personal_repo)
        return preset.get_config()


# === Slack Preset ===

SLACK_PRESET_INFO = {
    "name": "slack",
    "description": "Slack MCP server for messaging and channels",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-slack"],
    "recommended_tools": [
        "send_message",
        "post_message",
        "list_channels",
        "get_channel",
        "list_messages",
    ],
}


class SlackPreset(MCPPreset):
    """Slack MCP server preset.

    Provides access to Slack APIs for:
    - Sending messages to channels/users
    - Posting updates and notifications
    - Reading channel history
    - Managing channels

    Usage:
        config = SlackPreset.from_env()
        manager.add_server(config)

    Environment Variables:
        SLACK_BOT_TOKEN: Slack bot token (required)
        SLACK_TEAM_ID: Optional - restrict to specific team
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        team_id: Optional[str] = None,
    ):
        super().__init__(
            name="slack",
            description="Slack MCP server for messaging and channels",
        )
        self.bot_token = bot_token
        self.team_id = team_id

    def get_config(self) -> MCPServerConfig:
        """Get Slack MCP server configuration."""
        import os

        bot_token = self.bot_token or os.environ.get("SLACK_BOT_TOKEN", "")

        env = {}
        if bot_token:
            env["SLACK_BOT_TOKEN"] = bot_token
        if self.team_id:
            env["SLACK_TEAM_ID"] = self.team_id

        return MCPServerConfig(
            name="slack",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-slack"],
            env=env,
            description="Slack MCP server for messaging and channels",
        )

    @classmethod
    def from_env(cls, **overrides) -> MCPServerConfig:
        """Create Slack preset from environment variables.

        Args:
            **overrides: Override any preset values.

        Returns:
            MCPServerConfig for Slack MCP server.
        """
        import os

        bot_token = overrides.get("bot_token") or os.environ.get("SLACK_BOT_TOKEN")
        team_id = overrides.get("team_id") or os.environ.get("SLACK_TEAM_ID")

        preset = cls(bot_token=bot_token, team_id=team_id)
        return preset.get_config()


# === PostgreSQL Preset ===

POSTGRESQL_PRESET_INFO = {
    "name": "postgresql",
    "description": "PostgreSQL MCP server for database operations",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-postgres"],
    "recommended_tools": [
        "query",
        "execute",
        "list_tables",
        "describe_table",
    ],
}


class PostgreSQLPreset(MCPPreset):
    """PostgreSQL MCP server preset.

    Provides access to PostgreSQL databases for:
    - Running queries
    - Exploring schema (tables, columns)
    - Data retrieval and analysis

    Usage:
        config = PostgreSQLPreset.from_env()
        manager.add_server(config)

    Environment Variables:
        POSTGRES_CONNECTION_STRING: PostgreSQL connection string (required)
            Format: postgresql://user:pass@host:port/dbname
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
    ):
        super().__init__(
            name="postgresql",
            description="PostgreSQL MCP server for database operations",
        )
        self.connection_string = connection_string

    def get_config(self) -> MCPServerConfig:
        """Get PostgreSQL MCP server configuration."""
        import os

        conn_str = self.connection_string or os.environ.get("POSTGRES_CONNECTION_STRING", "")

        env = {}
        if conn_str:
            env["POSTGRES_CONNECTION_STRING"] = conn_str

        return MCPServerConfig(
            name="postgresql",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-postgres"],
            env=env,
            description="PostgreSQL MCP server for database operations",
        )

    @classmethod
    def from_env(cls, **overrides) -> MCPServerConfig:
        """Create PostgreSQL preset from environment variables.

        Args:
            **overrides: Override any preset values.

        Returns:
            MCPServerConfig for PostgreSQL MCP server.
        """
        import os

        conn_str = overrides.get("connection_string") or os.environ.get("POSTGRES_CONNECTION_STRING")

        preset = cls(connection_string=conn_str)
        return preset.get_config()


# === Preset Registry ===

@dataclass
class MCPPresetInfo:
    """Information about a preset."""
    name: str
    description: str
    transport: str
    command: str
    args: list[str]
    recommended_tools: list[str]
    env_vars: list[str]
    preset_class: type


class MCPPresets:
    """Registry and factory for MCP server presets.

    Provides:
    - List of available presets
    - Factory methods to create preset configs
    - Validation of preset requirements

    Usage:
        # List available presets
        MCPPresets.list_presets()

        # Create a preset config
        config = MCPPresets.create("github")

        # Add directly to config manager
        manager = MCPConfigManager()
        for preset_name in ["github", "slack"]:
            manager.add_server(MCPPresets.create(preset_name))
    """

    _presets: dict[str, MCPPresetInfo] = {
        "github": MCPPresetInfo(
            name="github",
            description="GitHub MCP server for code, issues, PRs",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            recommended_tools=[
                "create_issue",
                "list_issues", 
                "get_issue",
                "create_pull_request",
                "list_pulls",
                "get_file_contents",
                "create_file",
            ],
            env_vars=["GITHUB_TOKEN"],
            preset_class=GitHubPreset,
        ),
        "slack": MCPPresetInfo(
            name="slack",
            description="Slack MCP server for messaging and channels",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-slack"],
            recommended_tools=[
                "send_message",
                "post_message",
                "list_channels",
                "get_channel",
                "list_messages",
            ],
            env_vars=["SLACK_BOT_TOKEN"],
            preset_class=SlackPreset,
        ),
        "postgresql": MCPPresetInfo(
            name="postgresql",
            description="PostgreSQL MCP server for database operations",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-postgres"],
            recommended_tools=[
                "query",
                "execute",
                "list_tables",
                "describe_table",
            ],
            env_vars=["POSTGRES_CONNECTION_STRING"],
            preset_class=PostgreSQLPreset,
        ),
    }

    @classmethod
    def list_presets(cls) -> list[MCPPresetInfo]:
        """List all available presets.

        Returns:
            List of preset info objects.
        """
        return list(cls._presets.values())

    @classmethod
    def get_preset_info(cls, name: str) -> Optional[MCPPresetInfo]:
        """Get preset info by name.

        Args:
            name: Preset name.

        Returns:
            Preset info or None if not found.
        """
        return cls._presets.get(name)

    @classmethod
    def create(cls, name: str, **overrides) -> Optional[MCPServerConfig]:
        """Create a preset config.

        Args:
            name: Preset name.
            **overrides: Override preset values (e.g., token=...).

        Returns:
            MCPServerConfig or None if preset not found.
        """
        preset_info = cls._presets.get(name)
        if not preset_info:
            return None

        preset = preset_info.preset_class()
        return preset.get_config()

    @classmethod
    def validate_env(cls, name: str) -> tuple[bool, list[str]]:
        """Validate environment for a preset.

        Args:
            name: Preset name.

        Returns:
            Tuple of (is_valid, missing_env_vars).
        """
        preset_info = cls._presets.get(name)
        if not preset_info:
            return False, [f"Unknown preset: {name}"]

        import os
        missing = []
        for env_var in preset_info.env_vars:
            if not os.environ.get(env_var):
                missing.append(env_var)

        return len(missing) == 0, missing

    @classmethod
    def add_to_manager(
        cls,
        manager: Any,
        preset_names: list[str],
        skip_validation: bool = False,
    ) -> tuple[int, int]:
        """Add presets to a config manager.

        Args:
            manager: MCPConfigManager instance.
            preset_names: List of preset names to add.
            skip_validation: Skip environment variable validation.

        Returns:
            Tuple of (added_count, skipped_count).
        """
        added = 0
        skipped = 0

        for name in preset_names:
            is_valid, missing = cls.validate_env(name)
            if not is_valid and not skip_validation:
                skipped += 1
                continue

            config = cls.create(name)
            if config:
                manager.add_server(config)
                added += 1

        return added, skipped
