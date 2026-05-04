"""RalphLoop Implementation Context — Shared State Container.

The ImplementationContext is a thread-safe dataclass that holds all mutable
state during a RalphLoop execution cycle. It is passed between RalphLoop
states (PLAN→ACT→VERIFY→REFLECT) and used by agents to share information.

Features:
    - Thread-safe state access via threading.Lock
    - Tracks: current task, messages, tool results, test results, errors
    - Budget monitoring with 4-tier context degradation
    - Checkpoint/restore for recovery
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .orchestrator import Checkpoint, ContextTier
from .states import RalphState


@dataclass
class ImplementationContext:
    """Shared mutable state container for RalphLoop execution.

    This context is passed between states (PLAN→ACT→VERIFY→REFLECT) and
    used by agents to share information during the execution cycle.

    Attributes:
        task: Current task description or dict.
        messages: List of message dicts exchanged with LLM.
        tool_results: List of tool call results.
        test_results: List of test execution results.
        error_log: List of error messages encountered.
        checkpoint_info: Optional checkpoint data for recovery.
        context_window: Maximum context window size (tokens, default 100000).
        _lock: Internal lock for thread-safe mutations.

    Example:
        ctx = ImplementationContext(task="Create a web server")
        ctx.add_message("user", "Create a web server")
        result = ctx.add_tool_result("bash", output="server running on port 8000")
        messages = ctx.get_messages_for_llm()
        tier = ctx.budget_tier  # PEAK/GOOD/DEGRADING/POOR
    """

    task: str | dict[str, Any] = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    error_log: list[str] = field(default_factory=list)
    checkpoint_info: Optional[Checkpoint] = None
    context_window: int = 100000
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # Self-Evolution: learn from errors and recover
    _evolution_engine: Optional[Any] = field(default=None, repr=False)

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation history.

        Args:
            role: Message role (e.g., "user", "assistant", "system").
            content: Message content.
        """
        with self._lock:
            self.messages.append({
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            })

    def add_tool_result(
        self,
        tool_name: str,
        output: str,
        success: bool = True,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record a tool call result.

        Args:
            tool_name: Name of the tool called.
            output: Tool output or response.
            success: Whether the tool call succeeded.
            error: Error message if failed.

        Returns:
            The tool result dict that was added.
        """
        with self._lock:
            result = {
                "tool": tool_name,
                "output": output,
                "success": success,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            }
            self.tool_results.append(result)
            return result

    def add_test_result(
        self,
        test_name: str,
        passed: bool,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record a test execution result.

        Args:
            test_name: Name of the test.
            passed: Whether the test passed.
            duration_ms: Test duration in milliseconds.
            error: Error message if failed.

        Returns:
            The test result dict that was added.
        """
        with self._lock:
            result = {
                "test_name": test_name,
                "passed": passed,
                "duration_ms": duration_ms,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            }
            self.test_results.append(result)
            return result

    def get_messages_for_llm(self) -> list[dict[str, Any]]:
        """Get messages formatted for LLM consumption.

        Returns:
            List of message dicts with role and content keys.
        """
        with self._lock:
            return [
                {"role": m["role"], "content": m["content"]}
                for m in self.messages
            ]

    @property
    def budget_percent(self) -> float:
        """Current context usage percentage.

        Computed from messages length vs context window.
        """
        with self._lock:
            if self.context_window <= 0:
                return 0.0
            # Estimate usage based on accumulated messages
            # Rough estimate: ~4 chars per token
            total_chars = sum(
                len(m.get("content", "")) for m in self.messages
            )
            estimated_tokens = total_chars / 4
            return (estimated_tokens / self.context_window) * 100

    @property
    def budget_tier(self) -> ContextTier:
        """Current context budget tier.

        Returns:
            ContextTier enum: PEAK (<30%), GOOD (30-50%),
            DEGRADING (50-70%), POOR (70%+).
        """
        return ContextTier.from_usage(self.budget_percent)

    def log_error(self, error: str) -> None:
        """Add an error to the error log.

        Args:
            error: Error message to log.
        """
        with self._lock:
            self.error_log.append(
                f"[{datetime.now().isoformat()}] {error}"
            )

    def get_changed_files(self) -> list[str]:
        """Get list of files modified in this session (git diff --name-only).
        
        Returns:
            List of file paths that were modified.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        except Exception:
            pass
        return []

    def get_file_content(self, file_path: str) -> str | None:
        """Get content of a file.
        
        Args:
            file_path: Path to the file.
            
        Returns:
            File content as string, or None if not found/not readable.
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def checkpoint(self) -> Checkpoint:
        """Create a checkpoint of the current context state.

        Returns:
            Checkpoint dataclass with current state snapshot.
        """
        with self._lock:
            self.checkpoint_info = Checkpoint(
                timestamp=datetime.now().isoformat(),
                state=RalphState.PLAN,  # State managed by orchestrator
                task_index=0,
                retry_count=0,
                context_usage=self.budget_percent,
                task_queue=[{"description": str(self.task)}],
                error_log=self.error_log.copy(),
            )
            return self.checkpoint_info

    def restore(self, checkpoint: Checkpoint) -> bool:
        """Restore context from a checkpoint.

        Args:
            checkpoint: Checkpoint to restore from.

        Returns:
            True if restoration successful.
        """
        try:
            with self._lock:
                self.task = (
                    checkpoint.task_queue[0]["description"]
                    if checkpoint.task_queue
                    else ""
                )
                self.error_log = checkpoint.error_log.copy()
                self.checkpoint_info = checkpoint
            return True
        except Exception:
            return False

    def clear(self) -> None:
        """Clear all mutable state except task and context_window."""
        with self._lock:
            self.messages.clear()
            self.tool_results.clear()
            self.test_results.clear()
            self.error_log.clear()
            self.checkpoint_info = None
