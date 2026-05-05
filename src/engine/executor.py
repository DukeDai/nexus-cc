"""RalphExecutor - main loop for Nexus agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ToolExecutionError(Exception):
    """Raised when a tool execution fails."""

    def __init__(self, tool_name: str, reason: str):
        super().__init__(f"Tool '{tool_name}' failed: {reason}")
        self.tool_name = tool_name
        self.reason = reason


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


# ------------------------------------------------------------------
# Message types for LLM communication
# ------------------------------------------------------------------


@dataclass
class SystemMessage:
    """System message for LLM.

    Serializes to: {"role": "system", "content": "..."}
    """

    content: str

    def to_llm_dict(self) -> dict[str, Any]:
        """Convert to LLM API message format."""
        return {"role": "system", "content": self.content}


@dataclass
class UserMessage:
    """User message for LLM.

    Serializes to: {"role": "user", "content": "..."}
    """

    content: str

    def to_llm_dict(self) -> dict[str, Any]:
        """Convert to LLM API message format."""
        return {"role": "user", "content": self.content}


@dataclass
class AssistantMessage:
    """Assistant message for LLM.

    Serializes to: {"role": "assistant", "content": "...", "tool_calls": [...]}
    when there are tool calls, or just {"role": "assistant", "content": "..."}
    when there's text content.
    """

    content: str = ""
    tool_calls: list[Any] = field(default_factory=list)

    def to_llm_dict(self) -> dict[str, Any]:
        """Convert to LLM API message format."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            # Convert ToolCall objects to provider-native format
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
                for tc in self.tool_calls
            ]
        return msg


@dataclass
class ToolResultMessage:
    """Result message from a tool execution.

    Serializes to: {"role": "user", "content": "...", "name": "tool_name"}
    The "name" field references which tool was called (for Anthropic).
    OpenAI uses tool_call_id instead.
    """

    name: str  # Name of the tool that was executed
    content: str
    tool_call_id: str = ""  # ID of the tool call (OpenAI style)

    def to_llm_dict(self) -> dict[str, Any]:
        """Convert to LLM API message format."""
        msg = {
            "role": "user",
            "content": self.content,
            "name": self.name,
        }
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


def messages_to_llm_format(messages: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of message objects to LLM API format.

    Args:
        messages: List of message objects (SystemMessage, UserMessage,
                  AssistantMessage, ToolResultMessage)

    Returns:
        List of dictionaries suitable for LLM API calls.
    """
    return [msg.to_llm_dict() for msg in messages]


# ------------------------------------------------------------------
# RalphExecutor
# ------------------------------------------------------------------


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
        # Build initial message list
        if system_prompt is None:
            system_prompt = "You are Ralph, a helpful AI assistant."

        messages: list[Any] = [
            SystemMessage(system_prompt),
            UserMessage(task),
        ]

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

            # Convert messages to LLM API format
            messages_as_dicts = messages_to_llm_format(messages)

            # Attempt LLM completion with up to 3 retries
            response = None
            for attempt in range(3):
                try:
                    response = self.llm.complete(
                        messages_as_dicts,
                        tools=self.registry.definitions(),
                    )
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

            # Append assistant response to message history
            if response.content:
                messages.append(AssistantMessage(content=response.content))
                final_content = response.content

            # Handle tool calls
            if response.tool_calls:
                # Add assistant message with tool calls
                messages.append(AssistantMessage(tool_calls=response.tool_calls))

                # Execute each tool and append results
                for tc in response.tool_calls:
                    try:
                        result = self.registry.execute(tc.name, **tc.input)
                        if result.is_error:
                            raise ToolExecutionError(tc.name, result.error or "Unknown error")
                        messages.append(
                            ToolResultMessage(
                                name=tc.name,
                                content=result.output,
                                tool_call_id=tc.id,
                            )
                        )
                    except Exception as exc:
                        if isinstance(exc, ToolExecutionError):
                            error_msg = str(exc)
                        else:
                            error_msg = f"Tool '{tc.name}' raised {type(exc).__name__}: {exc}"
                        messages.append(
                            ToolResultMessage(
                                name=tc.name,
                                content=f"Error: {error_msg}",
                                tool_call_id=tc.id,
                            )
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
        if getattr(response, "finish_reason", None) in ("end_turn", "stop_sequence", "stop"):
            return True
        return False
