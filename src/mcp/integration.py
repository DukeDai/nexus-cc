"""RalphLoop → MCP Bridge for PLAN/VERIFY phases.

Bridges RalphLoop state machine with MCP servers, enabling:
- PLAN phase: Use MCP tools for spec generation, requirements analysis
- VERIFY phase: Use MCP tools for testing, validation, security scanning
- Context-aware tool selection based on RalphLoop state
- Result caching and fallback strategies
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from ralphloop.states import RalphState


class MCPBridgePhase(Enum):
    """Phases where MCP bridge is active."""
    PLAN = "plan"
    VERIFY = "verify"


@dataclass
class MCPBridgeConfig:
    """Configuration for RalphLoop ↔ MCP bridge.

    Attributes:
        enabled: Whether bridge is enabled.
        plan_servers: List of server names to use in PLAN phase.
        verify_servers: List of server names to use in VERIFY phase.
        cache_dir: Directory for caching MCP results.
        timeout: Timeout for MCP tool calls in seconds.
        fallback_enabled: Enable fallback strategies on failure.
        retry_count: Number of retries on MCP call failure.
    """
    enabled: bool = True
    plan_servers: list[str] = field(default_factory=lambda: ["github"])
    verify_servers: list[str] = field(default_factory=lambda: ["github"])
    cache_dir: Optional[Path] = None
    timeout: int = 60
    fallback_enabled: bool = True
    retry_count: int = 2


@dataclass
class MCPBridgeResult:
    """Result from an MCP tool call via bridge.

    Attributes:
        success: Whether the call succeeded.
        server: Server name that was called.
        tool: Tool name that was called.
        result: Raw result from MCP tool.
        error: Error message if failed.
        cached: Whether result was from cache.
        duration_ms: Call duration in milliseconds.
        phase: RalphLoop phase when call was made.
    """
    success: bool
    server: str
    tool: str
    result: Optional[Any] = None
    error: Optional[str] = None
    cached: bool = False
    duration_ms: float = 0.0
    phase: Optional[MCPBridgePhase] = None


class RalphLoopMCPBridge:
    """Bridge between RalphLoop state machine and MCP servers.

    This bridge enables RalphLoop to use MCP tools during PLAN and VERIFY
    phases. It handles:
    - Context-aware server selection based on current phase
    - Result caching for repeated queries
    - Fallback strategies on failure
    - Metrics collection for bridge analytics

    Example:
        bridge = RalphLoopMCPBridge(
            config=MCPBridgeConfig(
                plan_servers=["github", "filesystem"],
                verify_servers=["github"],
            ),
            config_manager=mcp_config_manager,
        )

        # In PLAN phase
        result = bridge.call_for_phase(
            RalphState.PLAN,
            "github",
            "create_issue",
            {"title": "Spec review needed"},
        )

        # In VERIFY phase
        result = bridge.call_for_phase(
            RalphState.VERIFY,
            "github",
            "list_issues",
            {"state": "open"},
        )
    """

    def __init__(
        self,
        config: Optional[MCPBridgeConfig] = None,
        config_manager: Optional[Any] = None,
        context_monitor: Optional[Callable[[], float]] = None,
    ):
        """Initialize RalphLoop ↔ MCP bridge.

        Args:
            config: Bridge configuration.
            config_manager: MCPConfigManager instance for server access.
            context_monitor: Callable returning context usage 0-100.
        """
        self.config = config or MCPBridgeConfig()
        self.config_manager = config_manager
        self.context_monitor = context_monitor or (lambda: 50.0)

        # Initialize cache
        if self.config.cache_dir:
            self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = {}
        self._load_cache()

        # Metrics
        self.total_calls: int = 0
        self.cache_hits: int = 0
        self.failed_calls: int = 0

    def _cache_key(self, server: str, tool: str, args: dict[str, Any]) -> str:
        """Generate cache key for a call."""
        # Sort args for consistent key
        sorted_args = json.dumps(args, sort_keys=True)
        return f"{server}:{tool}:{sorted_args}"

    def _load_cache(self) -> None:
        """Load cache from disk."""
        if not self.config.cache_dir:
            return
        cache_file = self.config.cache_dir / "mcp_bridge_cache.json"
        if cache_file.exists():
            try:
                self._cache = json.loads(cache_file.read_text())
            except json.JSONDecodeError:
                self._cache = {}

    def _save_cache(self) -> None:
        """Save cache to disk."""
        if not self.config.cache_dir:
            return
        cache_file = self.config.cache_dir / "mcp_bridge_cache.json"
        cache_file.write_text(json.dumps(self._cache, indent=2))

    def _get_cached(self, cache_key: str) -> Optional[Any]:
        """Get cached result."""
        if cache_key in self._cache:
            self.cache_hits += 1
            return self._cache[cache_key]
        return None

    def _set_cached(self, cache_key: str, result: Any) -> None:
        """Cache a result."""
        self._cache[cache_key] = {
            "result": result,
            "cached_at": datetime.now().isoformat(),
        }
        self._save_cache()

    def _call_mcporter(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
    ) -> MCPBridgeResult:
        """Call MCP tool via mcporter CLI.

        Args:
            server: Server name.
            tool: Tool name.
            args: Tool arguments.

        Returns:
            MCPBridgeResult with call outcome.
        """
        start = datetime.now()

        # Build mcporter command
        args_str = " ".join(f"{k}={v}" for k, v in args.items())
        full_tool = f"{server}.{tool}"
        cmd = [full_tool, args_str] if args_str else [full_tool]

        try:
            result = subprocess.run(
                ["npx", "-y", "mcporter", "call"] + cmd + ["--output", "json"],
                capture_output=True,
                text=True,
                timeout=self.config.timeout,
            )

            duration = (datetime.now() - start).total_seconds() * 1000

            if result.returncode == 0 and result.stdout.strip():
                try:
                    parsed = json.loads(result.stdout)
                    return MCPBridgeResult(
                        success=True,
                        server=server,
                        tool=tool,
                        result=parsed,
                        duration_ms=duration,
                    )
                except json.JSONDecodeError:
                    return MCPBridgeResult(
                        success=True,
                        server=server,
                        tool=tool,
                        result=result.stdout.strip(),
                        duration_ms=duration,
                    )
            else:
                return MCPBridgeResult(
                    success=False,
                    server=server,
                    tool=tool,
                    error=result.stderr or "Unknown error",
                    duration_ms=duration,
                )

        except subprocess.TimeoutExpired:
            duration = (datetime.now() - start).total_seconds() * 1000
            return MCPBridgeResult(
                success=False,
                server=server,
                tool=tool,
                error=f"Timeout after {self.config.timeout}s",
                duration_ms=duration,
            )
        except FileNotFoundError:
            duration = (datetime.now() - start).total_seconds() * 1000
            return MCPBridgeResult(
                success=False,
                server=server,
                tool=tool,
                error="mcporter CLI not found (npx required)",
                duration_ms=duration,
            )
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            return MCPBridgeResult(
                success=False,
                server=server,
                tool=tool,
                error=str(e),
                duration_ms=duration,
            )

    def _get_servers_for_phase(self, phase: RalphState) -> list[str]:
        """Get configured servers for a RalphLoop phase.

        Args:
            phase: RalphLoop state.

        Returns:
            List of server names to use.
        """
        if phase == RalphState.PLAN:
            return self.config.plan_servers
        elif phase == RalphState.VERIFY:
            return self.config.verify_servers
        return []

    def call_for_phase(
        self,
        phase: RalphState,
        server: str,
        tool: str,
        args: Optional[dict[str, Any]] = None,
        use_cache: bool = True,
    ) -> MCPBridgeResult:
        """Call an MCP tool for a specific RalphLoop phase.

        This is the main entry point for bridge tool calls.
        Handles caching, fallback, and metrics collection.

        Args:
            phase: RalphLoop phase (PLAN or VERIFY).
            server: Server name.
            tool: Tool name.
            args: Tool arguments.
            use_cache: Whether to use cached results.

        Returns:
            MCPBridgeResult with call outcome.
        """
        self.total_calls += 1
        args = args or {}

        # Check cache first
        if use_cache:
            cache_key = self._cache_key(server, tool, args)
            cached = self._get_cached(cache_key)
            if cached:
                result = MCPBridgeResult(
                    success=True,
                    server=server,
                    tool=tool,
                    result=cached["result"],
                    cached=True,
                    phase=MCPBridgePhase.PLAN if phase == RalphState.PLAN else MCPBridgePhase.VERIFY,
                )
                return result

        # Map RalphState to MCPBridgePhase
        bridge_phase = MCPBridgePhase.PLAN if phase == RalphState.PLAN else MCPBridgePhase.VERIFY

        # Make the call
        result = self._call_mcporter(server, tool, args)
        result.phase = bridge_phase

        if result.success:
            # Cache successful result
            if use_cache:
                cache_key = self._cache_key(server, tool, args)
                self._set_cached(cache_key, result.result)
        else:
            self.failed_calls += 1

            # Try fallback servers if enabled
            if self.config.fallback_enabled:
                servers_for_phase = self._get_servers_for_phase(phase)
                if server in servers_for_phase and len(servers_for_phase) > 1:
                    # Try other servers
                    for fallback_server in servers_for_phase:
                        if fallback_server != server:
                            fallback_result = self._call_mcporter(fallback_server, tool, args)
                            fallback_result.phase = bridge_phase
                            if fallback_result.success:
                                if use_cache:
                                    cache_key = self._cache_key(fallback_server, tool, args)
                                    self._set_cached(cache_key, fallback_result.result)
                                return fallback_result

        return result

    def plan_with_mcp(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute PLAN phase with MCP tools.

        Args:
            task: Task dict with description, requirements, etc.

        Returns:
            Dict with plan results.
        """
        servers = self._get_servers_for_phase(RalphState.PLAN)
        if not servers:
            return {"success": False, "error": "No plan servers configured"}

        results = []
        for server in servers:
            result = self.call_for_phase(
                RalphState.PLAN,
                server,
                "search",  # Generic search tool
                {"query": task.get("description", "")},
            )
            results.append({"server": server, "result": result})

        return {
            "success": any(r["result"].success for r in results),
            "results": results,
        }

    def verify_with_mcp(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute VERIFY phase with MCP tools.

        Args:
            task: Task dict with description, implementation, etc.

        Returns:
            Dict with verification results.
        """
        servers = self._get_servers_for_phase(RalphState.VERIFY)
        if not servers:
            return {"success": False, "error": "No verify servers configured"}

        results = []
        for server in servers:
            result = self.call_for_phase(
                RalphState.VERIFY,
                server,
                "list_issues",  # Generic list tool
                {"state": "open", "limit": 5},
            )
            results.append({"server": server, "result": result})

        return {
            "success": any(r["result"].success for r in results),
            "results": results,
        }

    def get_metrics(self) -> dict[str, Any]:
        """Get bridge metrics.

        Returns:
            Dict with metrics.
        """
        return {
            "total_calls": self.total_calls,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": self.cache_hits / self.total_calls if self.total_calls > 0 else 0,
            "failed_calls": self.failed_calls,
            "failure_rate": self.failed_calls / self.total_calls if self.total_calls > 0 else 0,
        }

    def clear_cache(self) -> None:
        """Clear the result cache."""
        self._cache = {}
        self._save_cache()
