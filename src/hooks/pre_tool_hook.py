"""Pre-Tool Hook for Nexus.

Hooks that run before a tool is executed. Allows inspection,
modification, or rejection of tool calls before they execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .hook_manager import HookContext, HookEvent


@dataclass
class PreToolContext:
    """Context for pre-tool hooks.

    Attributes:
        tool_name: Name of the tool being called.
        tool_args: Arguments being passed to the tool.
        session_id: Current session identifier.
        agent_name: Name of the agent making the call (if applicable).
        task_id: Current task identifier (if applicable).
    """
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None
    agent_name: Optional[str] = None
    task_id: Optional[str] = None
    cancelled: bool = False
    cancel_reason: Optional[str] = None

    def cancel(self, reason: str = "Rejected by pre-tool hook") -> None:
        """Cancel the tool execution.

        Args:
            reason: Reason for cancellation.
        """
        self.cancelled = True
        self.cancel_reason = reason


class PreToolHook:
    """Pre-tool execution hook.

    Hooks in this class run before a tool is executed. They receive
    PreToolContext and can inspect/modify arguments or cancel execution.

    Usage:
        def my_pre_tool_hook(ctx: PreToolContext) -> Optional[dict]:
            if ctx.tool_name == "dangerous_tool":
                ctx.cancel("Not allowed in this context")
            return None  # Return value is ignored for pre-hooks

        hook = PreToolHook()
        hook.register(my_pre_tool_hook, "block_dangerous")
    """

    def __init__(self, hook_manager: Optional[Any] = None):
        """Initialize pre-tool hook.

        Args:
            hook_manager: HookManager to use. Defaults to module default.
        """
        self._manager = hook_manager
        self._handlers: list[tuple[Callable[[PreToolContext], Any], str]] = []

    @property
    def manager(self) -> Any:
        """Get the hook manager."""
        if self._manager is None:
            from src.hooks import get_hook_manager
            self._manager = get_hook_manager()
        return self._manager

    def register(
        self,
        handler: Callable[[PreToolContext], Any],
        name: str,
        priority: int = 0,
    ) -> None:
        """Register a pre-tool hook handler.

        Args:
            handler: Function to call before tool execution.
            name: Unique name for this hook.
            priority: Higher priority runs first.
        """
        self._handlers.append((handler, name))
        # Also register with the hook manager
        wrapped_handler = self._wrap_handler(handler)
        self.manager.register(
            HookEvent.PRE_TOOL_USE,
            wrapped_handler,
            name=f"pre_tool.{name}",
            priority=priority,
        )

    def _wrap_handler(
        self,
        handler: Callable[[PreToolContext], Any],
    ) -> Callable[[HookContext], Any]:
        """Wrap a PreToolContext handler to work with HookContext."""
        def wrapped(ctx: HookContext) -> Any:
            pre_ctx = PreToolContext(
                tool_name=ctx.get("tool_name", ""),
                tool_args=ctx.get("tool_args", {}),
                session_id=ctx.get("session_id"),
                agent_name=ctx.get("agent_name"),
                task_id=ctx.get("task_id"),
            )
            return handler(pre_ctx)
        return wrapped

    def unregister(self, name: str) -> bool:
        """Unregister a pre-tool hook by name.

        Args:
            name: Name of the hook to remove.

        Returns:
            True if hook was found and removed.
        """
        self._handlers = [(h, n) for h, n in self._handlers if n != name]
        return self.manager.unregister(f"pre_tool.{name}")

    def should_proceed(self, context: PreToolContext) -> bool:
        """Check if tool should proceed based on hook results.

        Args:
            context: The pre-tool context.

        Returns:
            True if no hooks cancelled, False otherwise.
        """
        return not context.cancelled

    def execute(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        **kwargs: Any,
    ) -> PreToolContext:
        """Execute all pre-tool hooks.

        Args:
            tool_name: Name of the tool.
            tool_args: Arguments to the tool.
            **kwargs: Additional context.

        Returns:
            PreToolContext after all hooks have run.
        """
        ctx = self.manager.invoke_pre_tool(tool_name, tool_args, **kwargs)

        pre_ctx = PreToolContext(
            tool_name=tool_name,
            tool_args=tool_args,
            session_id=ctx.get("session_id"),
            agent_name=ctx.get("agent_name"),
            task_id=ctx.get("task_id"),
            cancelled=ctx.error is not None if ctx else False,
        )

        # Re-check using our internal tracking
        for handler, name in self._handlers:
            try:
                result = handler(pre_ctx)
                if pre_ctx.cancelled:
                    break
            except Exception as e:
                pre_ctx.cancelled = True
                pre_ctx.cancel_reason = str(e)
                break

        return pre_ctx


# Convenience function
def create_pre_tool_context(
    tool_name: str,
    tool_args: dict[str, Any],
    **kwargs: Any,
) -> PreToolContext:
    """Create a PreToolContext with the given parameters.

    Args:
        tool_name: Name of the tool.
        tool_args: Tool arguments.
        **kwargs: Additional context fields.

    Returns:
        PreToolContext instance.
    """
    return PreToolContext(tool_name=tool_name, tool_args=tool_args, **kwargs)
