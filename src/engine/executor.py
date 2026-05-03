"""RalphExecutor - main loop for Nexus agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

if True:  # noqa: C901
    pass


@dataclass
class LoopResult:
    """Result of a single execution loop.

    Attributes:
        messages: Full list of messages exchanged during the loop.
        turns: Number of turns executed.
        complete: Whether the loop terminated because it was complete.
        final_content: Text content of the final assistant message, if any.
    """

    messages: list[Any] = field(default_factory=list)
    turns: int = 0
    complete: bool = False
    final_content: str = ""


class RalphExecutor:
    """Main agent loop executor.

    Coordinates LLM calls, tool execution via the registry, hook firing,
    and context budget monitoring.
    """

    def __init__(
        self,
        llm_client: Any,
        registry: Any,
        hooks: Any,
        context_monitor: Any,
    ) -> None:
        """Initialize the executor.

        Args:
            llm_client: LLM client (must support .complete(messages, tools)).
            registry: ToolRegistry instance.
            hooks: HookManager instance (must support pre_loop, post_loop,
                   pre_step, post_step hooks).
            context_monitor: ContextBudgetMonitor instance (must support
                             .check() and .update()).
        """
        self.llm = llm_client
        self.registry = registry
        self.hooks = hooks
        self.context_monitor = context_monitor

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def run_loop(
        self,
        task: str,
        max_turns: int = 100,
        system_prompt: str | None = None,
    ) -> LoopResult:
        """Run the agent loop for the given task.

        Args:
            task: The user task / question to answer.
            max_turns: Maximum number of turns before stopping.
            system_prompt: System prompt to prepend. Uses a default if None.

        Returns:
            LoopResult with messages, turn count, completion flag, and
            final content.
        """
        from dataclasses import replace
        from typing import Any

        # Build initial message list
        if system_prompt is None:
            system_prompt = "You are Ralph, a helpful AI assistant."

        messages: list[Any] = [SystemMessage(system_prompt), UserMessage(task)]

        complete = False
        final_content = ""

        # Pre-loop hook
        self.hooks.pre_loop(task)

        turn = 0
        last_error = None

        for turn in range(max_turns):
            # Context budget check
            if self.context_monitor.check(messages):
                break

            # Pre-step hook
            self.hooks.pre_step(messages)

            # Attempt LLM completion with up to 3 retries
            response = None
            for attempt in range(3):
                try:
                    response = self.llm.complete(messages, tools=self.registry.definitions())
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt < 2:
                        continue
                    # After 3 failures, append error and break
                    messages.append(
                        SystemMessage(f"LLM call failed after 3 attempts: {exc}")
                    )
                    break

            if response is None:
                break

            # Append assistant response
            if response.content:
                messages.append(AssistantMessage(response.content))
                final_content = response.content

            # Handle tool calls
            if response.tool_calls:
                messages.append(AssistantMessage(tool_calls=response.tool_calls))

                for tc in response.tool_calls:
                    result = self.registry.execute(tc.name, **tc.input)
                    messages.append(
                        ToolResultMessage(tool_call_id=tc.id, content=result.output)
                    )

            # Post-step hook
            self.hooks.post_step(messages)

            # Check for completion
            if self._is_complete(response):
                complete = True
                break

            # Update context budget
            self.context_monitor.update(messages)

        # Post-loop hook
        self.hooks.post_loop(messages, turn + 1)

        return LoopResult(
            messages=messages,
            turns=turn + 1,
            complete=complete,
            final_content=final_content,
        )

    # -------------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------------

    def _is_complete(self, response: Any) -> bool:
        """Determine whether the response signals the end of the loop.

        A response is considered complete when it has text content and
        either no tool calls or the model explicitly signals stop.
        """
        # Stop if there's content and no tool calls
        if response.content and not response.tool_calls:
            return True
        # If the model explicitly says it is done (e.g. stops with stop_reason)
        if getattr(response, "stop_reason", None) in ("end_turn", "stop_sequence"):
            return True
        return False


# ------------------------------------------------------------------
# Minimal message types (can be replaced by importing from a schema lib)
# ------------------------------------------------------------------

from dataclasses import dataclass as _dataclass, field as _field


@_dataclass
class SystemMessage:
    content: str


@_dataclass
class UserMessage:
    content: str


@_dataclass
class AssistantMessage:
    content: str = ""
    tool_calls: list[Any] = _field(default_factory=list)


@_dataclass
class ToolResultMessage:
    tool_call_id: str
    content: str
