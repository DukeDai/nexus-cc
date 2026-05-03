#!/usr/bin/env python3
"""Nexus TUI — Real-time RalphLoop State Visualization.

A terminal UI that shows RalphLoop state transitions in real-time.
This is a key innovation vs Claude Code: users SEE the state machine.

┌──────────────────────────────────────────────────────────────┐
│  RalphLoop Status                                            │
├──────────────────────────────────────────────────────────────┤
│  State: [ACT  ████████░░░░░░░░░░░] 45%                      │
│  Context: PEAK (28%) | Tools: 12 | Turns: 4                  │
│  Git: main (dirty)                                          │
├──────────────────────────────────────────────────────────────┤
│  [PLAN]  →  [ACT]  →  [VERIFY]  →  [REFLECT]               │
│            ████████░░░░░░░░░░░░░                             │
│            ImplementerAgent: running...                      │
├──────────────────────────────────────────────────────────────┤
│  Recent Activity:                                           │
│  ✓ write_file: src/auth.py (234 lines)                      │
│  ✓ bash: pytest tests/auth_test.py - 3/3 passed             │
│  → read_file: src/auth.py (lines 1-50)                     │
└──────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import os
import sys
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

# Try rich first, fall back to plain ANSI
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
    from rich.live import Live
    from rich.layout import Layout
    from rich.text import Text
    from rich.style import Style
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


# ─── RalphLoop State ───────────────────────────────────────────────────────────

class LoopState(Enum):
    """RalphLoop states."""
    IDLE = "idle"
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    REFLECT = "reflect"
    COMMIT = "commit"
    RETRY = "retry"
    ESCALATE = "escalate"
    ABORT = "abort"
    COMPLETE = "complete"


# ─── ANSI Color Codes ──────────────────────────────────────────────────────────

class ANSI:
    """ANSI escape codes for terminal colors."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Bright foreground
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"
    
    # Background colors
    BG_BLACK = "\033[40m"
    BG_BLUE = "\033[44m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    
    # Cursor control
    CLEAR_SCREEN = "\033[2J"
    CLEAR_EOL = "\033[K"
    HOME = "\033[H"
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"


# ─── State Colors ─────────────────────────────────────────────────────────────

STATE_COLORS = {
    LoopState.IDLE: ANSI.BRIGHT_BLACK,
    LoopState.PLAN: ANSI.BRIGHT_CYAN,
    LoopState.ACT: ANSI.BRIGHT_GREEN,
    LoopState.VERIFY: ANSI.BRIGHT_YELLOW,
    LoopState.REFLECT: ANSI.BRIGHT_MAGENTA,
    LoopState.COMMIT: ANSI.BRIGHT_BLUE,
    LoopState.RETRY: ANSI.BRIGHT_YELLOW,
    LoopState.ESCALATE: ANSI.BRIGHT_RED,
    LoopState.ABORT: ANSI.RED,
    LoopState.COMPLETE: ANSI.BRIGHT_GREEN,
}


# ─── Activity Log ─────────────────────────────────────────────────────────────

@dataclass
class ActivityEntry:
    """A single activity log entry."""
    timestamp: datetime
    icon: str  # ✓ → ✗ ⚠
    message: str
    detail: str = ""
    is_error: bool = False


# ─── RalphLoop TUI ─────────────────────────────────────────────────────────────

class RalphTUI:
    """Terminal UI for RalphLoop state visualization.
    
    Supports two modes:
    - RICH mode: Uses rich library for beautiful formatting
    - ANSI mode: Pure ANSI escape codes (no dependencies)
    """
    
    STATE_ORDER = [
        LoopState.PLAN,
        LoopState.ACT,
        LoopState.VERIFY,
        LoopState.REFLECT,
    ]
    
    def __init__(self, use_rich: bool = True):
        self.use_rich = use_rich and RICH_AVAILABLE
        self.console = Console() if self.use_rich else None
        
        # State
        self._state: LoopState = LoopState.IDLE
        self._context_pct: float = 0.0
        self._tool_calls: int = 0
        self._turns: int = 0
        self._git_branch: str = ""
        self._git_dirty: bool = False
        self._subagent_status: str = ""
        self._activity_log: list[ActivityEntry] = []
        self._last_update: float = time.time()
        self._lock = threading.Lock()
        
        # Progress
        self._state_progress: float = 0.0  # 0.0-1.0 for current state
        self._message: str = ""
        
        # Rendering
        self._render_count: int = 0
        
        # ANSI mode: we manage cursor ourselves
        self._ansi_enabled = not self.use_rich
        
        if self._ansi_enabled:
            # Check if terminal supports ANSI
            self._supports_ansi = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
        else:
            self._supports_ansi = False
    
    # ─── Public API ─────────────────────────────────────────────────────────
    
    def set_state(self, state: LoopState) -> None:
        """Update the current RalphLoop state."""
        with self._lock:
            self._state = state
            self._state_progress = 0.0
            self._last_update = time.time()
        self._add_activity("→", f"State: {state.value}")
        self.render()
    
    def set_context_budget(self, pct: float) -> None:
        """Update context budget percentage."""
        with self._lock:
            self._context_pct = min(100.0, max(0.0, pct))
        self.render()
    
    def increment_tool_calls(self, tool_name: str, success: bool = True) -> None:
        """Record a tool call."""
        with self._lock:
            self._tool_calls += 1
            icon = "✓" if success else "✗"
            self._add_activity(icon, f"{tool_name}", "tool call")
        self.render()
    
    def increment_turns(self) -> None:
        """Record an LLM turn."""
        with self._lock:
            self._turns += 1
        self.render()
    
    def set_subagent_status(self, status: str) -> None:
        """Update subagent status (e.g., 'ImplementerAgent: running...')."""
        with self._lock:
            self._subagent_status = status
        self.render()
    
    def set_git_info(self, branch: str, dirty: bool) -> None:
        """Update git information."""
        with self._lock:
            self._git_branch = branch
            self._git_dirty = dirty
        self.render()
    
    def set_message(self, msg: str) -> None:
        """Set the current status message."""
        with self._lock:
            self._message = msg
        self.render()
    
    def set_state_progress(self, pct: float) -> None:
        """Update progress within the current state (0.0-1.0)."""
        with self._lock:
            self._state_progress = min(1.0, max(0.0, pct))
        self.render()
    
    def add_activity(self, icon: str, message: str, detail: str = "", 
                     is_error: bool = False) -> None:
        """Add an activity log entry."""
        self._add_activity(icon, message, detail, is_error)
        self.render()
    
    def clear_activities(self) -> None:
        """Clear the activity log."""
        with self._lock:
            self._activity_log.clear()
        self.render()
    
    def render(self) -> None:
        """Render the TUI (called after any state change)."""
        with self._lock:
            if self._ansi_enabled and self._supports_ansi:
                self._render_ansi()
            elif self.use_rich:
                self._render_rich()
            # Otherwise: silent (no output)
    
    def render_once(self) -> str:
        """Render a single frame and return the string (for testing)."""
        with self._lock:
            return self._build_output()
    
    # ─── ANSI Rendering ──────────────────────────────────────────────────────
    
    def _render_ansi(self) -> None:
        """Render using ANSI escape codes (no dependencies)."""
        output = self._build_output()
        # Move cursor home and clear screen
        sys.stdout.write(f"{ANSI.CLEAR_SCREEN}{ANSI.HOME}")
        sys.stdout.write(output)
        sys.stdout.write(ANSI.HOME)
        sys.stdout.flush()
        self._render_count += 1
    
    def _build_output(self) -> str:
        """Build the TUI output string (ANSI mode)."""
        lines = []
        width = 70
        
        # ── Header ──
        lines.append(self._colored(f"╭{'─' * (width - 2)}╮", ANSI.BRIGHT_BLUE))
        
        # Title with git info
        git_str = f" │ Git: {self._git_branch}"
        if self._git_dirty:
            git_str += " (dirty)"
        title = f"  RalphLoop  │ Context: {self._context_bar()}{git_str}"
        lines.append(self._colored(f"│{self._pad(title, width - 2)}│", ANSI.BRIGHT_WHITE))
        lines.append(self._colored(f"├{'─' * (width - 2)}┤", ANSI.BRIGHT_BLUE))
        
        # ── State Display ──
        state_color = STATE_COLORS.get(self._state, ANSI.WHITE)
        state_label = f"State: [{self._state.value.upper()}"
        progress_bar = self._progress_bar(self._state_progress)
        state_line = f"{self._colored(state_label, state_color)} {progress_bar}]"
        lines.append(self._colored(f"│  {self._pad(state_line, width - 4)}│", ANSI.WHITE))
        
        # ── Metrics ──
        metrics = f"Context: {self._context_pct:.0f}% │ Tools: {self._tool_calls} │ Turns: {self._turns}"
        if self._subagent_status:
            metrics += f" │ {self._subagent_status}"
        lines.append(self._colored(f"│  {self._pad(metrics, width - 4)}│", ANSI.DIM))
        
        # ── State Machine Flow ──
        lines.append(self._colored(f"├{'─' * (width - 2)}┤", ANSI.BRIGHT_BLUE))
        flow = self._state_flow()
        lines.append(self._colored(f"│  {self._pad(flow, width - 4)}│", ANSI.WHITE))
        
        # ── Message ──
        if self._message:
            lines.append(self._colored(f"│  {self._pad(self._message, width - 4)}│", ANSI.CYAN))
        
        # ── Activity Log ──
        lines.append(self._colored(f"├{'─' * (width - 2)}┤", ANSI.BRIGHT_BLUE))
        log_header = "  Recent Activity:"
        lines.append(self._colored(f"│{self._pad(log_header, width - 2)}│", ANSI.BRIGHT_YELLOW))
        
        recent = self._activity_log[-6:] if len(self._activity_log) > 6 else self._activity_log
        if recent:
            for entry in recent:
                icon = entry.icon
                msg = f"{icon} {entry.message}"
                if entry.detail:
                    msg += f" ({entry.detail})"
                color = ANSI.RED if entry.is_error else ANSI.WHITE
                lines.append(self._colored(f"│  {self._pad(msg, width - 4)}│", color))
        else:
            lines.append(self._colored(f"│  {self._pad('(no activity yet)', width - 4)}│", ANSI.DIM))
        
        # ── Footer ──
        lines.append(self._colored(f"╰{'─' * (width - 2)}╯", ANSI.BRIGHT_BLUE))
        
        return "\n".join(lines)
    
    def _state_flow(self) -> str:
        """Build the state flow visualization."""
        states = ["PLAN", "ACT", "VERIFY", "REFLECT"]
        current_state = self._state.name
        
        arrows = ["→", "→", "→", ""]
        parts = []
        
        for i, state in enumerate(states):
            color = ANSI.BRIGHT_GREEN if state == current_state else ANSI.DIM
            arrow = arrows[i] if i < len(arrows) else ""
            if state == current_state:
                parts.append(f"[{self._colored(state, STATE_COLORS.get(LoopState[state], ANSI.WHITE))}]")
            else:
                parts.append(f"[{self._colored(state, color)}]")
            if arrow:
                parts.append(self._colored(arrow, ANSI.DIM))
        
        return " ".join(parts)
    
    def _context_bar(self) -> str:
        """Build the context budget bar."""
        pct = self._context_pct
        if pct < 30:
            color = ANSI.BRIGHT_GREEN
        elif pct < 50:
            color = ANSI.BRIGHT_YELLOW
        elif pct < 70:
            color = ANSI.YELLOW
        else:
            color = ANSI.BRIGHT_RED
        
        filled = int(pct / 5)  # 20 segments max
        empty = 20 - filled
        bar = "█" * filled + "░" * empty
        return self._colored(f"{bar} {pct:.0f}%", color)
    
    def _progress_bar(self, pct: float, width: int = 20) -> str:
        """Build a progress bar for current state."""
        filled = int(pct * width)
        empty = width - filled
        bar = "█" * filled + "░" * empty
        return bar
    
    def _colored(self, text: str, color: str) -> str:
        """Apply ANSI color to text."""
        if not self._supports_ansi:
            return text
        return f"{color}{text}{ANSI.RESET}"
    
    def _pad(self, text: str, width: int) -> str:
        """Pad text to specified width."""
        # Strip ANSI codes for length calculation
        clean = self._strip_ansi(text)
        padding = max(0, width - len(clean))
        return text + " " * padding
    
    def _strip_ansi(self, text: str) -> str:
        """Strip ANSI codes from text."""
        import re
        return re.sub(r'\033\[[0-9;]*m', '', text)
    
    # ─── Rich Rendering ──────────────────────────────────────────────────────
    
    def _render_rich(self) -> None:
        """Render using the rich library."""
        if not self.console:
            return
        
        layout = Layout()
        
        # This would use rich's Live display for real-time updates
        # For now, fall back to simple console output
        self.console.clear()
        
        # Print header
        self.console.print(Panel(
            f"[bold cyan]RalphLoop[/bold cyan] │ "
            f"Context: {self._context_pct:.0f}% │ "
            f"Tools: {self._tool_calls} │ "
            f"Turns: {self._turns}",
            title="Status",
            border_style="blue"
        ))
        
        # Print state
        state_color = STATE_COLORS.get(self._state, "white")
        self.console.print(f"[{state_color}]{self._state.value.upper()}[/{state_color}]")
    
    # ─── Activity Log ─────────────────────────────────────────────────────────
    
    def _add_activity(self, icon: str, message: str, detail: str = "",
                       is_error: bool = False) -> None:
        """Add entry to activity log (thread-safe)."""
        entry = ActivityEntry(
            timestamp=datetime.now(),
            icon=icon,
            message=message[:60],
            detail=detail[:40],
            is_error=is_error,
        )
        self._activity_log.append(entry)
        # Keep last 50 entries
        if len(self._activity_log) > 50:
            self._activity_log = self._activity_log[-50:]


# ─── Nexus TUI Integration ─────────────────────────────────────────────────────

class NexusTUI:
    """High-level TUI for Nexus, integrating with RalphLoop states."""
    
    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()
        self._tui = RalphTUI(use_rich=False)  # ANSI mode by default
        self._running = False
        self._update_thread: threading.Thread | None = None
    
    def start(self) -> None:
        """Start the TUI in a background thread."""
        if self._running:
            return
        self._running = True
        
        # Detect git info
        try:
            import subprocess
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(self.workdir), capture_output=True, text=True, timeout=5
            )
            branch = result.stdout.strip() if result.returncode == 0 else ""
            
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.workdir), capture_output=True, text=True, timeout=5
            )
            dirty = len(result.stdout.strip()) > 0 if result.returncode == 0 else False
            
            self._tui.set_git_info(branch, dirty)
        except Exception:
            pass
        
        self._tui.set_state(LoopState.IDLE)
        self._tui.render()
    
    def stop(self) -> None:
        """Stop the TUI."""
        self._running = False
        if self._update_thread and self._update_thread.is_alive():
            self._update_thread.join(timeout=1.0)
        
        # Show cursor again
        if self._tui._supports_ansi:
            sys.stdout.write(ANSI.SHOW_CURSOR + ANSI.RESET)
            sys.stdout.flush()
    
    def update_from_loop(
        self,
        state: LoopState,
        context_pct: float,
        tool_calls: int,
        turns: int,
        subagent_status: str = "",
    ) -> None:
        """Update TUI from RalphLoop state."""
        self._tui.set_state(state)
        self._tui.set_context_budget(context_pct)
        self._tui._tool_calls = tool_calls
        self._tui._turns = turns
        if subagent_status:
            self._tui.set_subagent_status(subagent_status)
        self._tui.render()
    
    def log(self, icon: str, message: str, is_error: bool = False) -> None:
        """Log an activity."""
        self._tui.add_activity(icon, message, is_error=is_error)
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, *args):
        self.stop()


# ─── Demo ─────────────────────────────────────────────────────────────────────

def demo():
    """Demo the TUI with simulated activity."""
    tui = RalphTUI(use_rich=False)
    tui._supports_ansi = True  # Force ANSI for demo
    tui.set_git_info("main", True)
    
    print(ANSI.HIDE_CURSOR)
    
    try:
        # Simulate RalphLoop lifecycle
        states = [
            (LoopState.PLAN, 0.0, "Analyzing task..."),
            (LoopState.PLAN, 0.5, "Breaking down into subtasks..."),
            (LoopState.PLAN, 1.0, "Plan complete: 5 steps"),
            (LoopState.ACT, 0.0, "Starting ImplementerAgent..."),
            (LoopState.ACT, 0.2, "ImplementerAgent: running..."),
            (LoopState.ACT, 0.4, "ImplementerAgent: running..."),
            (LoopState.ACT, 0.6, "ImplementerAgent: running..."),
            (LoopState.ACT, 0.8, "ImplementerAgent: running..."),
            (LoopState.ACT, 1.0, "ImplementerAgent: complete"),
            (LoopState.VERIFY, 0.0, "Running tests..."),
            (LoopState.VERIFY, 1.0, "All 5 tests passed"),
            (LoopState.REFLECT, 0.0, "Reviewing implementation..."),
            (LoopState.REFLECT, 1.0, "Quality: Good"),
            (LoopState.COMMIT, 0.0, "Committing changes..."),
            (LoopState.COMMIT, 1.0, "Committed: abc1234"),
            (LoopState.COMPLETE, 1.0, "Task complete!"),
        ]
        
        activities = [
            ("✓", "read_file: SPEC.md", "200 lines"),
            ("✓", "write_file: src/auth.py", "234 lines"),
            ("✓", "bash: pytest tests/", "3/3 passed"),
            ("→", "apply_diff: src/main.py", "2 hunks"),
            ("✓", "git add -A", "3 files"),
            ("✓", "git commit", "abc1234"),
        ]
        
        for i, (state, progress, msg) in enumerate(states):
            tui.set_state(state)
            tui.set_state_progress(progress)
            tui.set_message(msg)
            tui.set_context_budget(20.0 + i * 3)
            tui._turns = i + 1
            tui._tool_calls = i + 2
            tui.render()
            time.sleep(0.15)
        
        for icon, msg, detail in activities:
            tui.add_activity(icon, msg, detail)
            tui.render()
            time.sleep(0.1)
        
        time.sleep(1)
        
    finally:
        print(ANSI.SHOW_CURSOR + ANSI.RESET)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        print("RalphTUI module. Run with --demo to see demo.")
