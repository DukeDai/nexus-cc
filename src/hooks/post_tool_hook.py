"""Post-Tool Hook for Nexus.

Hooks that run after a tool has executed. Allows inspection of
results, error handling, and result modification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..hooks.hook_manager import HookContext, HookEvent


@dataclass
class PostToolContext:
    """Context for post-tool hooks.

    Attributes:
        tool_name: Name of the tool that was executed.
        tool_args: Arguments that were passed to the tool.
        result: The result returned by the tool (may be None on error).
        error: Any exception that occurred during execution.
        session_id: Current session identifier.
        agent_name: Name of the agent that made the call.
        task_id: Current task identifier.
        duration_seconds: How long the tool took to execute.
    """
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[Exception] = None
    session_id: Optional[str] = None
    agent_name: Optional[str] = None
    task_id: Optional[str] = None
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        """Check if the tool execution succeeded."""
        return self.error is None

    @property
    def failed(self) -> bool:
        """Check if the tool execution failed."""
        return self.error is not None


class PostToolHook:
    """Post-tool execution hook.

    Hooks in this class run after a tool has executed. They receive
    PostToolContext with the result or error and can:
    - Log or record results
    - Transform the result
    - Handle errors
    - Trigger follow-up actions

    Usage:
        def my_post_tool_hook(ctx: PostToolContext) -> Optional[dict]:
            if ctx.failed:
                logger.error(f"Tool {ctx.tool_name} failed: {ctx.error}")
            else:
                logger.info(f"Tool {ctx.tool_name} succeeded")
            return None  # Can return modified result

        hook = PostToolHook()
        hook.register(my_post_tool_hook, "log_results")
    """

    def __init__(self, hook_manager: Optional[Any] = None):
        """Initialize post-tool hook.

        Args:
            hook_manager: HookManager to use. Defaults to module default.
        """
        self._manager = hook_manager
        self._handlers: list[tuple[Callable[[PostToolContext], Any], str]] = []

    @property
    def manager(self) -> Any:
        """Get the hook manager."""
        if self._manager is None:
            from src.hooks import get_hook_manager
            self._manager = get_hook_manager()
        return self._manager

    def register(
        self,
        handler: Callable[[PostToolContext], Any],
        name: str,
        priority: int = 0,
    ) -> None:
        """Register a post-tool hook handler.

        Args:
            handler: Function to call after tool execution.
            name: Unique name for this hook.
            priority: Higher priority runs first.
        """
        self._handlers.append((handler, name))
        # Also register with the hook manager
        wrapped_handler = self._wrap_handler(handler)
        self.manager.register(
            HookEvent.POST_TOOL_USE,
            wrapped_handler,
            name=f"post_tool.{name}",
            priority=priority,
        )

    def _wrap_handler(
        self,
        handler: Callable[[PostToolContext], Any],
    ) -> Callable[[HookContext], Any]:
        """Wrap a PostToolContext handler to work with HookContext."""
        def wrapped(ctx: HookContext) -> Any:
            post_ctx = PostToolContext(
                tool_name=ctx.get("tool_name", ""),
                tool_args=ctx.get("tool_args", {}),
                result=ctx.result,
                error=ctx.error,
                session_id=ctx.get("session_id"),
                agent_name=ctx.get("agent_name"),
                task_id=ctx.get("task_id"),
                duration_seconds=ctx.get("duration_seconds", 0.0),
            )
            return handler(post_ctx)
        return wrapped

    def unregister(self, name: str) -> bool:
        """Unregister a post-tool hook by name.

        Args:
            name: Name of the hook to remove.

        Returns:
            True if hook was found and removed.
        """
        self._handlers = [(h, n) for h, n in self._handlers if n != name]
        return self.manager.unregister(f"post_tool.{name}")

    def execute(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result: Any = None,
        error: Optional[Exception] = None,
        **kwargs: Any,
    ) -> PostToolContext:
        """Execute all post-tool hooks.

        Args:
            tool_name: Name of the tool.
            tool_args: Arguments to the tool.
            result: The tool's result.
            error: Any error that occurred.
            **kwargs: Additional context.

        Returns:
            PostToolContext after all hooks have run.
        """
        ctx = self.manager.invoke_post_tool(
            tool_name, tool_args, result=result, error=error, **kwargs
        )

        post_ctx = PostToolContext(
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            error=error,
            session_id=ctx.get("session_id") if ctx else None,
            agent_name=ctx.get("agent_name") if ctx else None,
            task_id=ctx.get("task_id") if ctx else None,
            duration_seconds=ctx.get("duration_seconds", 0.0) if ctx else 0.0,
        )

        # Run internal handlers
        for handler, name in self._handlers:
            try:
                handler(post_ctx)
            except Exception:
                pass  # Hook errors shouldn't propagate

        return post_ctx

    def transform_result(
        self,
        handler: Callable[[PostToolContext], Any],
    ) -> Callable[[PostToolContext], Any]:
        """Decorator to create a result-transforming hook.

        The handler should return the (potentially modified) result.

        Usage:
            @post_tool_hook.transform_result
            def add_metadata(ctx: PostToolContext) -> Any:
                if ctx.success:
                    return {"data": ctx.result, "meta": {"tool": ctx.tool_name}}
                return ctx.result
        """
        def decorator(
            ctx: PostToolContext,
        ) -> Any:
            return handler(ctx)
        return decorator


# Convenience function
def create_post_tool_context(
    tool_name: str,
    tool_args: dict[str, Any],
    result: Any = None,
    error: Optional[Exception] = None,
    **kwargs: Any,
) -> PostToolContext:
    """Create a PostToolContext with the given parameters.

    Args:
        tool_name: Name of the tool.
        tool_args: Tool arguments.
        result: Tool result.
        error: Any exception.
        **kwargs: Additional context fields.

    Returns:
        PostToolContext instance.
    """
    return PostToolContext(
        tool_name=tool_name,
        tool_args=tool_args,
        result=result,
        error=error,
        **kwargs,
    )
