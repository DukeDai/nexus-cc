"""Hook Manager for Nexus.

Central hook management system supporting multiple event types:
- PreToolUse / PostToolUse: Before/after tool execution
- PreAgent / PostAgent: Before/after agent execution
- PreCommit / PostCommit: Before/after commit operations
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional, Any


class HookEvent(Enum):
    """Supported hook events."""
    PRE_TOOL_USE = auto()
    POST_TOOL_USE = auto()
    PRE_AGENT = auto()
    POST_AGENT = auto()
    PRE_COMMIT = auto()
    POST_COMMIT = auto()

    # Internal RalphLoop events (mapped to hook events)
    PRE_STATE_CHANGE = auto()
    POST_STATE_CHANGE = auto()

    def __repr__(self) -> str:
        return f"HookEvent.{self.name}"


@dataclass
class HookContext:
    """Context passed to hook handlers.

    Attributes:
        event: The hook event type.
        timestamp: When the hook was invoked (epoch seconds).
        data: Event-specific data dict.
        result: For post-hooks, the result of the operation.
        error: Any error that occurred.
    """
    event: HookEvent
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[Exception] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Get data value with optional default."""
        return self.data.get(key, default)


# Type alias for hook handler functions
HookHandler = Callable[[HookContext], Optional[Any]]


@dataclass
class HookRegistration:
    """A registered hook with metadata."""
    event: HookEvent
    handler: HookHandler
    name: str
    priority: int = 0  # Higher priority runs first
    enabled: bool = True
    description: str = ""


class HookManager:
    """Central hook management system.

    Manages registration and invocation of hooks for various events.
    Hooks are executed in priority order within each event type.

    Usage:
        manager = HookManager()

        # Register a hook
        def my_pre_tool_hook(ctx: HookContext) -> None:
            print(f"Tool {ctx.get('tool_name')} will execute")

        manager.register(HookEvent.PRE_TOOL_USE, my_pre_tool_hook, "my_hook")

        # Invoke hooks for an event
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE, data={"tool_name": "read"})
        result = manager.invoke(ctx)
    """

    def __init__(self):
        """Initialize the hook manager."""
        self._hooks: dict[HookEvent, list[HookRegistration]] = {
            event: [] for event in HookEvent
        }
        self._global_hooks: list[HookRegistration] = []
        self._event_counts: dict[HookEvent, int] = {e: 0 for e in HookEvent}

    def register(
        self,
        event: HookEvent,
        handler: HookHandler,
        name: str,
        priority: int = 0,
        description: str = "",
    ) -> None:
        """Register a hook handler for an event.

        Args:
            event: The event to hook into.
            handler: Callable that takes HookContext and returns optional result.
            name: Unique name for this hook.
            priority: Execution priority (higher runs first).
            description: Human-readable description.
        """
        registration = HookRegistration(
            event=event,
            handler=handler,
            name=name,
            priority=priority,
            description=description,
        )

        self._hooks[event].append(registration)
        self._hooks[event].sort(key=lambda r: -r.priority)  # Sort desc by priority

    def register_global(
        self,
        handler: HookHandler,
        name: str,
        priority: int = 0,
        description: str = "",
    ) -> None:
        """Register a hook that runs for ALL events.

        Args:
            handler: Callable that takes HookContext and returns optional result.
            name: Unique name for this hook.
            priority: Execution priority (higher runs first).
            description: Human-readable description.
        """
        registration = HookRegistration(
            event=HookEvent.PRE_TOOL_USE,  # Placeholder, overridden by invoke
            handler=handler,
            name=name,
            priority=priority,
            description=description,
        )
        self._global_hooks.append(registration)
        self._global_hooks.sort(key=lambda r: -r.priority)

    def unregister(self, name: str) -> bool:
        """Unregister a hook by name.

        Args:
            name: Name of the hook to remove.

        Returns:
            True if hook was found and removed.
        """
        # Check global hooks
        for i, reg in enumerate(self._global_hooks):
            if reg.name == name:
                self._global_hooks.pop(i)
                return True

        # Check event-specific hooks
        for event in HookEvent:
            for i, reg in enumerate(self._hooks[event]):
                if reg.name == name:
                    self._hooks[event].pop(i)
                    return True

        return False

    def enable(self, name: str) -> bool:
        """Enable a hook by name.

        Args:
            name: Name of the hook to enable.

        Returns:
            True if hook was found and enabled.
        """
        return self._set_enabled(name, True)

    def disable(self, name: str) -> bool:
        """Disable a hook by name.

        Args:
            name: Name of the hook to disable.

        Returns:
            True if hook was found and disabled.
        """
        return self._set_enabled(name, False)

    def _set_enabled(self, name: str, enabled: bool) -> bool:
        """Set enabled state for a hook."""
        for reg in self._global_hooks:
            if reg.name == name:
                reg.enabled = enabled
                return True

        for event in HookEvent:
            for reg in self._hooks[event]:
                if reg.name == name:
                    reg.enabled = enabled
                    return True

        return False

    def invoke(self, context: HookContext) -> Optional[Any]:
        """Invoke all handlers for an event.

        Args:
            context: HookContext with event and data.

        Returns:
            Result from last handler that returned non-None, or None.
        """
        event = context.event
        self._event_counts[event] = self._event_counts.get(event, 0) + 1

        result = None

        # Invoke global hooks first
        for reg in self._global_hooks:
            if reg.enabled:
                try:
                    r = reg.handler(context)
                    if r is not None:
                        result = r
                except Exception as e:
                    context.error = e

        # Invoke event-specific hooks
        for reg in self._hooks[event]:
            if reg.enabled:
                try:
                    r = reg.handler(context)
                    if r is not None:
                        result = r
                except Exception as e:
                    context.error = e

        return result

    def invoke_pre_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        **kwargs: Any,
    ) -> HookContext:
        """Convenience method to invoke PRE_TOOL_USE hooks.

        Args:
            tool_name: Name of the tool.
            tool_args: Arguments being passed to tool.
            **kwargs: Additional context data.

        Returns:
            HookContext after invocation.
        """
        context = HookContext(
            event=HookEvent.PRE_TOOL_USE,
            data={
                "tool_name": tool_name,
                "tool_args": tool_args,
                **kwargs,
            },
        )
        self.invoke(context)
        return context

    def invoke_post_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result: Any,
        error: Optional[Exception] = None,
        **kwargs: Any,
    ) -> HookContext:
        """Convenience method to invoke POST_TOOL_USE hooks.

        Args:
            tool_name: Name of the tool.
            tool_args: Arguments that were passed to tool.
            result: Result from tool execution.
            error: Any error that occurred.
            **kwargs: Additional context data.

        Returns:
            HookContext after invocation.
        """
        context = HookContext(
            event=HookEvent.POST_TOOL_USE,
            data={
                "tool_name": tool_name,
                "tool_args": tool_args,
                **kwargs,
            },
            result=result,
            error=error,
        )
        self.invoke(context)
        return context

    def invoke_pre_agent(
        self,
        agent_name: str,
        task: dict[str, Any],
        **kwargs: Any,
    ) -> HookContext:
        """Convenience method to invoke PRE_AGENT hooks.

        Args:
            agent_name: Name of the agent.
            task: Task being executed.
            **kwargs: Additional context data.

        Returns:
            HookContext after invocation.
        """
        context = HookContext(
            event=HookEvent.PRE_AGENT,
            data={
                "agent_name": agent_name,
                "task": task,
                **kwargs,
            },
        )
        self.invoke(context)
        return context

    def invoke_post_agent(
        self,
        agent_name: str,
        task: dict[str, Any],
        result: Any,
        error: Optional[Exception] = None,
        **kwargs: Any,
    ) -> HookContext:
        """Convenience method to invoke POST_AGENT hooks.

        Args:
            agent_name: Name of the agent.
            task: Task that was executed.
            result: Result from agent execution.
            error: Any error that occurred.
            **kwargs: Additional context data.

        Returns:
            HookContext after invocation.
        """
        context = HookContext(
            event=HookEvent.POST_AGENT,
            data={
                "agent_name": agent_name,
                "task": task,
                **kwargs,
            },
            result=result,
            error=error,
        )
        self.invoke(context)
        return context

    def invoke_pre_commit(
        self,
        message: str,
        files: list[str],
        **kwargs: Any,
    ) -> HookContext:
        """Convenience method to invoke PRE_COMMIT hooks.

        Args:
            message: Commit message.
            files: List of files being committed.
            **kwargs: Additional context data.

        Returns:
            HookContext after invocation.
        """
        context = HookContext(
            event=HookEvent.PRE_COMMIT,
            data={
                "message": message,
                "files": files,
                **kwargs,
            },
        )
        self.invoke(context)
        return context

    def invoke_post_commit(
        self,
        message: str,
        files: list[str],
        commit_sha: str,
        **kwargs: Any,
    ) -> HookContext:
        """Convenience method to invoke POST_COMMIT hooks.

        Args:
            message: Commit message.
            files: List of files that were committed.
            commit_sha: SHA of the created commit.
            **kwargs: Additional context data.

        Returns:
            HookContext after invocation.
        """
        context = HookContext(
            event=HookEvent.POST_COMMIT,
            data={
                "message": message,
                "files": files,
                "commit_sha": commit_sha,
                **kwargs,
            },
        )
        self.invoke(context)
        return context

    def list_hooks(self, event: Optional[HookEvent] = None) -> list[dict[str, Any]]:
        """List registered hooks.

        Args:
            event: Filter by event type. None for all.

        Returns:
            List of hook info dicts.
        """
        hooks_info = []

        if event is None:
            # Include global hooks
            for reg in self._global_hooks:
                hooks_info.append({
                    "name": reg.name,
                    "event": "GLOBAL",
                    "priority": reg.priority,
                    "enabled": reg.enabled,
                    "description": reg.description,
                })
            # Include event-specific hooks
            for e in HookEvent:
                for reg in self._hooks[e]:
                    hooks_info.append({
                        "name": reg.name,
                        "event": e.name,
                        "priority": reg.priority,
                        "enabled": reg.enabled,
                        "description": reg.description,
                    })
        else:
            for reg in self._hooks[event]:
                hooks_info.append({
                    "name": reg.name,
                    "event": reg.event.name,
                    "priority": reg.priority,
                    "enabled": reg.enabled,
                    "description": reg.description,
                })

        return hooks_info

    def get_stats(self) -> dict[str, Any]:
        """Get hook invocation statistics.

        Returns:
            Dict with event counts and hook counts.
        """
        return {
            "event_counts": dict(self._event_counts),
            "total_hooks": sum(len(hooks) for hooks in self._hooks.values()),
            "global_hooks": len(self._global_hooks),
            "by_event": {
                event.name: len(self._hooks[event])
                for event in HookEvent
            },
        }

    def clear(self) -> None:
        """Remove all registered hooks."""
        self._hooks = {event: [] for event in HookEvent}
        self._global_hooks = []
        self._event_counts = {e: 0 for e in HookEvent}


# Module-level convenience instance
_default_manager: Optional[HookManager] = None


def get_hook_manager() -> HookManager:
    """Get the default module-level hook manager.

    Returns:
        The default HookManager instance.
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = HookManager()
    return _default_manager


def reset_hook_manager() -> None:
    """Reset the default hook manager."""
    global _default_manager
    _default_manager = None
