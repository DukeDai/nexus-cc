"""Non-blocking command input handler for interactive TUI.

Uses a background thread with readchar for non-blocking keyboard input,
a command queue for thread-safe communication, and input mode switching
to handle different interaction contexts (command palette, escalation, etc.).

Features:
    - Non-blocking keyboard input in background thread
    - Command queue for thread-safe communication
    - Command history with up/down arrow navigation
    - Tab completion for partial commands
    - Multiple input modes (LINE, COMMAND, ESCALATION)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional


class InputMode(Enum):
    """Current input mode controlling which keys are intercepted."""
    LINE = auto()        # Direct line input (normal mode)
    COMMAND = auto()     # Command palette mode with history and completion
    ESCALATION = auto()  # Escalation option selection (1-4)


@dataclass
class Command:
    """A parsed command from user input."""
    name: str
    args: list[str]
    raw: str


class CommandInputHandler:
    """Non-blocking command input handler.

    Uses a separate input thread with readchar for cross-platform
    non-blocking keyboard read. Commands are queued for consumption
    by the main TUI thread.

    Attributes:
        mode: Current input mode controlling which commands are accepted.
        on_command: Callback when a command is parsed.
        on_ctrl_c: Callback when Ctrl+C is pressed.
        collecting: Current partial input being typed (exposed for tab completion).
        commands: List of available commands for completion (set by app.py).
    """

    def __init__(
        self,
        on_command: Callable[[Command], None],
        on_ctrl_c: Callable[[], None],
    ):
        self._on_command = on_command
        self._on_ctrl_c = on_ctrl_c
        self._queue: deque[Command] = deque(maxlen=100)
        self._history: list[str] = []
        self._history_index: int = -1
        self._mode = InputMode.LINE
        self._running = False
        self._input_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._collecting = ""
        # Commands for tab completion (set externally by NexusTUI)
        self.commands: list[str] = []

    def start(self) -> None:
        """Start the input handling thread."""
        self._running = True
        self._input_thread = threading.Thread(target=self._input_loop, daemon=True)
        self._input_thread.start()

    def stop(self) -> None:
        """Stop the input handling thread."""
        self._running = False
        if self._input_thread:
            self._input_thread.join(timeout=1.0)

    @property
    def mode(self) -> InputMode:
        """Current input mode."""
        with self._lock:
            return self._mode

    def set_mode(self, mode: InputMode) -> None:
        """Change input mode (affects which keys are intercepted)."""
        with self._lock:
            self._mode = mode
        self._collecting = ""

    @property
    def collecting(self) -> str:
        """Current partial input string (for tab completion UI)."""
        with self._lock:
            return self._collecting

    @property
    def history(self) -> list[str]:
        """Command history list."""
        with self._lock:
            return list(self._history)

    def get_next_command(self) -> Optional[Command]:
        """Get and remove the next command from queue (non-blocking)."""
        with self._lock:
            if self._queue:
                return self._queue.popleft()
        return None

    def peek_command(self) -> Optional[Command]:
        """Peek at next command without removing it."""
        with self._lock:
            if self._queue:
                return self._queue[0]
        return None

    def get_completions(self, partial: str) -> list[str]:
        """Get list of commands that match the partial input."""
        if not partial:
            return list(self.commands)
        partial_lower = partial.lower()
        return [cmd for cmd in self.commands if cmd.startswith(partial_lower)]

    def _input_loop(self) -> None:
        """Background input loop - runs in separate thread."""
        try:
            import readchar
        except ImportError:
            return

        while self._running:
            ch = readchar.readchar()
            if ch == readchar.key.CTRL_C:
                self._on_ctrl_c()
                continue
            if ch == readchar.key.CTRL_P:
                self.set_mode(InputMode.COMMAND)
                continue
            self._handle_char(ch)

    def _handle_char(self, ch: str) -> None:
        """Handle a single character input."""
        mode = self._mode  # local copy for thread safety

        if mode == InputMode.ESCALATION:
            if ch in '1234':
                option_map = {
                    '1': 'force-merge',
                    '2': 'rewrite',
                    '3': 'abandon',
                    '4': 'decompose',
                }
                cmd = Command(name=option_map[ch], args=[], raw=ch)
                with self._lock:
                    self._queue.append(cmd)
                self.set_mode(InputMode.LINE)
            return

        if mode == InputMode.COMMAND:
            self._handle_command_char(ch)
            return

        # LINE mode
        if ch in ('\n', '\r'):
            if self._collecting:
                raw = self._collecting
                parts = raw.strip().split()
                if parts:
                    cmd = Command(name=parts[0].lower(), args=parts[1:], raw=raw)
                    with self._lock:
                        self._queue.append(cmd)
                    with self._lock:
                        self._history.append(raw)
                        self._history_index = len(self._history)
                self._collecting = ""
        elif ch == '\x7f':  # Backspace
            if self._collecting:
                self._collecting = self._collecting[:-1]
        elif len(ch) == 1 and ord(ch) >= 32:  # Printable ASCII
            self._collecting += ch

    def _handle_command_char(self, ch: str) -> None:
        """Handle input in COMMAND mode (with history and completion)."""
        import readchar

        # Arrow key sequences
        if ch == '\x1b':  # Escape - could be start of arrow sequence
            # Peek at next char to check for arrow
            try:
                next_ch = readchar.readchar()
                if next_ch == '[':
                    direction = readchar.readchar()
                    if direction == 'A':  # Up
                        self._navigate_history(-1)
                        return
                    elif direction == 'B':  # Down
                        self._navigate_history(1)
                        return
                # Not an arrow sequence, treat escape as cancel
                self.set_mode(InputMode.LINE)
                return
            except Exception:
                self.set_mode(InputMode.LINE)
                return

        if ch == '\t':  # Tab - completion
            completions = self.get_completions(self._collecting)
            if len(completions) == 1:
                self._collecting = completions[0]
            elif len(completions) > 1:
                # Could show completions here - for now just complete to longest common prefix
                pass
            return

        if ch in ('\n', '\r'):
            if self._collecting:
                raw = self._collecting
                parts = raw.strip().split()
                if parts:
                    cmd = Command(name=parts[0].lower(), args=parts[1:], raw=raw)
                    with self._lock:
                        self._queue.append(cmd)
                    with self._lock:
                        self._history.append(raw)
                        self._history_index = len(self._history)
                self._collecting = ""
            self.set_mode(InputMode.LINE)
        elif ch == '\x1b':  # Escape - cancel command mode
            self._collecting = ""
            self.set_mode(InputMode.LINE)
        elif ch == '\x7f':  # Backspace
            if self._collecting:
                self._collecting = self._collecting[:-1]
        elif len(ch) == 1 and ord(ch) >= 32:  # Printable ASCII
            self._collecting += ch

    def _navigate_history(self, direction: int) -> None:
        """Navigate command history with up/down arrows.

        Args:
            direction: -1 for up (older), 1 for down (newer)
        """
        with self._lock:
            if not self._history:
                return

            new_index = self._history_index + direction

            if direction == -1:  # Up
                if self._history_index == -1:
                    self._history_index = len(self._history) - 1
                elif new_index < 0:
                    new_index = 0
                else:
                    self._history_index = new_index
            else:  # Down
                if self._history_index == -1:
                    return
                if new_index >= len(self._history):
                    self._history_index = -1
                    self._collecting = ""
                    return
                else:
                    self._history_index = new_index

            if self._history_index != -1:
                self._collecting = self._history[self._history_index]
