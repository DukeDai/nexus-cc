"""Integration module for connecting hooks with RalphLoop orchestrator.

This module provides adapters to integrate the hook system with RalphLoop's
existing callbacks (on_state_change, on_warning, on_escalation).

Usage:
    from hooks.integration import RalphLoopHookAdapter

    adapter = RalphLoopHookAdapter(hook_manager)
    adapter.attach_to_orchestrator(orchestrator)
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from hooks.hook_manager import HookEvent, HookManager, HookContext


class RalphLoopHookAdapter:
    """Adapter to connect HookManager with RalphLoop orchestrator.

    Maps RalphLoop callbacks to hook events:
    - on_state_change(old, new) -> PRE/POST_STATE_CHANGE hooks
    - on_warning(tier, message) -> Warning hooks
    - on_escalation(context) -> Escalation hooks

    Usage:
        adapter = RalphLoopHookAdapter(hook_manager)
        adapter.attach_to_orchestrator(orchestrator)
    """

    def __init__(self, hook_manager: Optional[HookManager] = None):
        """Initialize adapter.

        Args:
            hook_manager: HookManager instance. Defaults to global manager.
        """
        if hook_manager is None:
            from src.hooks import get_hook_manager
            hook_manager = get_hook_manager()
        self.hook_manager = hook_manager
        self._orchestrator = None

    def attach_to_orchestrator(self, orchestrator: Any) -> None:
        """Attach hook handlers to a RalphLoop orchestrator.

        This replaces the orchestrator's callback methods with versions
        that also invoke the hook system.

        Args:
            orchestrator: RalphLoop instance to attach to.
        """
        self._orchestrator = orchestrator

        # Store original callbacks
        original_state_change = orchestrator.on_state_change
        original_warning = orchestrator.on_warning
        original_escalation = orchestrator.on_escalation

        # Wrap on_state_change
        def wrapped_state_change(old_state: Any, new_state: Any) -> None:
            # Invoke pre-state-change hook
            ctx = HookContext(
                event=HookEvent.PRE_STATE_CHANGE,
                data={
                    "old_state": old_state,
                    "new_state": new_state,
                    "orchestrator": orchestrator,
                },
            )
            self.hook_manager.invoke(ctx)

            # Call original if set
            if original_state_change:
                original_state_change(old_state, new_state)

            # Invoke post-state-change hook
            ctx_post = HookContext(
                event=HookEvent.POST_STATE_CHANGE,
                data={
                    "old_state": old_state,
                    "new_state": new_state,
                    "orchestrator": orchestrator,
                },
            )
            self.hook_manager.invoke(ctx_post)

        # Wrap on_warning
        def wrapped_warning(tier: Any, message: str) -> None:
            # Invoke warning hook
            ctx = HookContext(
                event=HookEvent.PRE_AGENT,  # Use PRE_AGENT for warnings
                data={
                    "tier": tier,
                    "message": message,
                    "orchestrator": orchestrator,
                },
            )
            self.hook_manager.invoke(ctx)

            # Call original if set
            if original_warning:
                original_warning(tier, message)

        # Wrap on_escalation
        def wrapped_escalation(context: dict[str, Any]) -> Any:
            # Invoke pre-commit hook for escalation (semantically similar)
            ctx = HookContext(
                event=HookEvent.PRE_COMMIT,
                data={
                    "escalation_context": context,
                    "orchestrator": orchestrator,
                },
            )
            self.hook_manager.invoke(ctx)

            # Call original if set
            if original_escalation:
                return original_escalation(context)
            return None

        # Attach wrapped callbacks
        orchestrator.on_state_change = wrapped_state_change
        orchestrator.on_warning = wrapped_warning
        orchestrator.on_escalation = wrapped_escalation

    def detach_from_orchestrator(self, orchestrator: Any) -> None:
        """Detach hooks from orchestrator (restore original callbacks).

        Note: This requires the original callbacks to have been stored
        by attach_to_orchestrator. For now, this is a no-op as we don't
        have access to the originals.

        Args:
            orchestrator: RalphLoop instance to detach from.
        """
        # Would need to store originals to properly restore
        pass

    def create_state_change_handler(
        self,
        on_transition: Optional[Callable[[Any, Any], None]] = None,
    ) -> Callable[[Any, Any], None]:
        """Create a state change handler for use with RalphLoop.

        Args:
            on_transition: Optional callback for state transitions.

        Returns:
            Handler function suitable for RalphLoop.on_state_change.
        """
        def handler(old_state: Any, new_state: Any) -> None:
            ctx = HookContext(
                event=HookEvent.POST_STATE_CHANGE,
                data={
                    "old_state": old_state,
                    "new_state": new_state,
                    "orchestrator": self._orchestrator,
                },
            )
            self.hook_manager.invoke(ctx)

            if on_transition:
                on_transition(old_state, new_state)

        return handler

    def create_warning_handler(
        self,
        on_warning: Optional[Callable[[Any, str], None]] = None,
    ) -> Callable[[Any, str], None]:
        """Create a warning handler for use with RalphLoop.

        Args:
            on_warning: Optional callback for warnings.

        Returns:
            Handler function suitable for RalphLoop.on_warning.
        """
        def handler(tier: Any, message: str) -> None:
            ctx = HookContext(
                event=HookEvent.PRE_AGENT,
                data={
                    "tier": tier,
                    "message": message,
                    "orchestrator": self._orchestrator,
                },
            )
            self.hook_manager.invoke(ctx)

            if on_warning:
                on_warning(tier, message)

        return handler


class HookAwareOrchestrator:
    """Mixin or wrapper that adds hook support to RalphLoop.

    Use as a mixin with RalphLoop or wrap an existing orchestrator.

    Example:
        from ralphloop.orchestrator import RalphLoop

        # Wrap existing orchestrator
        aware = HookAwareOrchestrator(orchestrator, hook_manager)
    """

    def __init__(
        self,
        orchestrator: Any,
        hook_manager: Optional[HookManager] = None,
    ):
        """Initialize with an orchestrator.

        Args:
            orchestrator: RalphLoop instance to wrap.
            hook_manager: HookManager instance. Defaults to global.
        """
        self._orchestrator = orchestrator
        self._adapter = RalphLoopHookAdapter(hook_manager)
        self._adapter.attach_to_orchestrator(orchestrator)

    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to wrapped orchestrator."""
        return getattr(self._orchestrator, name)

    def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Run the orchestrator with hook integration."""
        # Invoke pre-agent hooks for the entire run
        ctx = HookContext(
            event=HookEvent.PRE_AGENT,
            data={
                "action": "run",
                "task_queue": self._orchestrator.task_queue,
            },
        )
        self._adapter.hook_manager.invoke(ctx)

        result = self._orchestrator.run(*args, **kwargs)

        # Invoke post-agent hooks
        ctx_post = HookContext(
            event=HookEvent.POST_AGENT,
            data={
                "action": "run",
                "result": result,
            },
            result=result,
        )
        self._adapter.hook_manager.invoke(ctx_post)

        return result
