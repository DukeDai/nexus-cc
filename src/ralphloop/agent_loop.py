"""RalphLoop Agent Loop — Real LLM-driven closed loop.

This module implements the core closed-loop execution engine that drives
RalphLoop states (PLAN→ACT→VERIFY→REFLECT) by calling the LLM and executing
tools until completion.

Key insight: Each RalphLoop state is powered by this real LLM loop.
The orchestrator manages transitions; this loop does the actual work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .implementation_context import ImplementationContext
from .states import RalphState


@dataclass
class LoopResult:
    """Result of a single agent loop execution within a RalphLoop state.

    Attributes:
        complete: Whether the loop terminated because it was complete.
        final_content: Text content of the final assistant message, if any.
        turns: Number of LLM calls made.
        tool_calls: Total tool calls executed.
        context: The ImplementationContext after execution.
    """
    complete: bool = False
    final_content: str = ""
    turns: int = 0
    tool_calls: int = 0
    context: Optional[ImplementationContext] = None


@dataclass
class AgentLoopConfig:
    """Configuration for the agent loop.

    Attributes:
        max_turns: Maximum LLM calls per state (default 20).
        tool_timeout: Default timeout for tool execution in seconds (default 60).
        context_window: Context window size for budget tracking (default 100000).
        stop_on_content: Stop when LLM returns content with no tool calls (default True).
    """
    max_turns: int = 20
    tool_timeout: int = 60
    context_window: int = 100000
    stop_on_content: bool = True


class ToolExecutor:
    """Executes tool calls and returns results.

    This is the tool side of the closed loop. It maps tool names to
    actual implementations and returns string results for the LLM.
    """

    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()

    def execute(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool and return its result as a string."""
        import subprocess
        import re

        try:
            if tool_name == "bash":
                return self._bash(tool_args["command"], tool_args.get("timeout", 30))
            elif tool_name == "read_file":
                return self._read_file(tool_args["path"], tool_args.get("offset", 1), tool_args.get("limit", 500))
            elif tool_name == "write_file":
                return self._write_file(tool_args["path"], tool_args["content"])
            elif tool_name == "glob":
                return self._glob(tool_args["pattern"], tool_args.get("base_dir", str(self.workdir)))
            elif tool_name == "grep":
                return self._grep(tool_args["pattern"], tool_args.get("path", str(self.workdir)), tool_args.get("file_glob"))
            elif tool_name == "apply_diff":
                return self._apply_diff(tool_args["path"], tool_args["diff"])
            elif tool_name == "tdd_test":
                return self._tdd_test(tool_args["test_path"], tool_args["impl_path"],
                                      tool_args["test_code"], tool_args["impl_code"])
            elif tool_name == "git_commit":
                return self._git_commit(tool_args["message"], tool_args.get("push", False))
            else:
                return f"ERROR: Unknown tool '{tool_name}'"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    def _bash(self, command: str, timeout: int) -> str:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(self.workdir)
        )
        output = f"[exit {result.returncode}]\n"
        if result.stdout:
            output += f"STDOUT:\n{result.stdout[:3000]}"
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr[:1000]}"
        return output

    def _read_file(self, path: str, offset: int, limit: int) -> str:
        p = (self.workdir / path).resolve()
        if not p.exists():
            return f"ERROR: File not found: {p}"
        try:
            with open(p) as f:
                lines = f.readlines()
            start = max(0, offset - 1)
            end = start + limit
            content = "".join(lines[start:end])
            if len(lines) > limit:
                content += f"\n... [{len(lines)} total lines, showing {offset}-{end}]"
            return content
        except Exception as e:
            return f"ERROR reading {p}: {e}"

    def _write_file(self, path: str, content: str) -> str:
        p = (self.workdir / path).resolve()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w") as f:
                f.write(content)
            return f"OK: Wrote {len(content)} bytes to {p}"
        except Exception as e:
            return f"ERROR writing {p}: {e}"

    def _glob(self, pattern: str, base_dir: str) -> str:
        import fnmatch
        p = Path(base_dir).resolve()
        matches = list(p.glob(pattern))
        if not matches:
            return f"No files matching {pattern} in {p}"
        return "\n".join(f"{m.relative_to(p)}" for m in matches[:100])

    def _grep(self, pattern: str, path: str, file_glob: str | None) -> str:
        import fnmatch
        p = Path(path).resolve()
        results = []
        for f in p.rglob(file_glob or "*"):
            if not f.is_file():
                continue
            try:
                with open(f) as fh:
                    for i, line in enumerate(fh, 1):
                        if pattern.lower() in line.lower():
                            results.append(f"{f}:{i}: {line.rstrip()}")
            except:
                pass
        if not results:
            return f"No matches for '{pattern}' in {p}"
        return "\n".join(results[:50])

    def _apply_diff(self, path: str, diff: str) -> str:
        """Apply a unified diff to a file with robust parsing."""
        import re
        p = (self.workdir / path).resolve()
        if not p.exists():
            return f"ERROR: File not found: {p}"

        with open(p) as f:
            original_lines = [line.rstrip('\n') for line in f.readlines()]

        diff_lines = diff.split("\n")

        # Parse all hunks
        hunks = []
        i = 0
        while i < len(diff_lines):
            line = diff_lines[i]
            if line.startswith("@@ "):
                m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if not m:
                    i += 1
                    continue
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1

                hunk_content = []
                i += 1
                while i < len(diff_lines):
                    line = diff_lines[i]
                    if line.startswith("@@ "):
                        i -= 1
                        break
                    hunk_content.append(line)
                    i += 1

                while hunk_content and hunk_content[-1] == "":
                    hunk_content.pop()

                hunks.append({
                    'old_start': old_start,
                    'old_count': old_count,
                    'new_start': new_start,
                    'new_count': new_count,
                    'content': hunk_content
                })
            else:
                i += 1

        if not hunks:
            return f"ERROR: No valid hunks found in diff for {p}"

        patched_lines = original_lines[:]
        hunk_results = []
        total_removed = 0
        total_added = 0

        for hunk_idx, hunk in enumerate(hunks):
            old_start = hunk['old_start']
            old_count = hunk['old_count']
            new_count = hunk['new_count']
            hunk_content = hunk['content']

            hunk_lines = []
            for hl in hunk_content:
                if hl.startswith("-"):
                    hunk_lines.append(("del", hl[1:]))
                elif hl.startswith("+"):
                    hunk_lines.append(("add", hl[1:]))
                else:
                    hunk_lines.append(("ctx", hl[1:] if len(hl) > 1 else ""))

            # Classify edits within hunk body
            edits = []
            j = 0
            while j < len(hunk_lines):
                typ, text = hunk_lines[j]
                if typ == "ctx":
                    edits.append(("cnt", text))
                    j += 1
                elif typ == "del":
                    # Check if next is add (replacement)
                    if j + 1 < len(hunk_lines) and hunk_lines[j + 1][0] == "add":
                        edits.append(("rep", text, hunk_lines[j + 1][1]))
                        j += 2
                    else:
                        edits.append(("del", text))
                        j += 1
                elif typ == "add":
                    edits.append(("ins", text))
                    j += 1

            # Rebuild hunk
            cursor = max(0, old_start - 1)
            rebuild = []
            deletions = []
            insertions = []
            replacements = 0

            for edit in edits:
                if edit[0] == "cnt":
                    cursor += 1
                    rebuild.append(edit[1])
                elif edit[0] == "rep":
                    cursor += 1
                    rebuild.append(edit[2])
                    replacements += 1
                elif edit[0] == "del":
                    deletions.append((cursor, edit[1]))
                    cursor += 1
                elif edit[0] == "ins":
                    count_before = len(rebuild)
                    rebuild.insert(count_before, edit[1])
                    insertions.append((count_before, edit[1]))

            # Apply deletions in reverse order
            for edit_pos, _ in sorted(deletions, key=lambda x: x[0], reverse=True):
                adjusted_pos = edit_pos - sum(1 for d in deletions if d[0] < edit_pos)
                if 0 <= adjusted_pos < len(patched_lines):
                    patched_lines.pop(adjusted_pos)

            hunk_results.append({
                'idx': hunk_idx,
                'success': True,
                'replacements': replacements,
                'insertions': len(insertions),
                'deletions': len(deletions),
            })
            total_removed += len(deletions)
            total_added += len(insertions) + replacements

        # Write back
        try:
            with open(p, "w") as f:
                f.write("\n".join(patched_lines) + "\n")
            return (f"OK: Applied {len(hunks)} hunks, "
                    f"{total_removed} removed, {total_added} added to {p}")
        except Exception as e:
            return f"ERROR writing {p}: {e}"

    def _tdd_test(self, test_path: str, impl_path: str, test_code: str, impl_code: str) -> str:
        """Write test + impl, run pytest."""
        import subprocess
        results = []
        # Write test
        tp = (self.workdir / test_path).resolve()
        try:
            tp.parent.mkdir(parents=True, exist_ok=True)
            with open(tp, "w") as f:
                f.write(test_code)
            results.append(f"OK: Wrote test to {tp}")
        except Exception as e:
            return f"ERROR writing test {tp}: {e}"

        # Write impl
        ip = (self.workdir / impl_path).resolve()
        try:
            ip.parent.mkdir(parents=True, exist_ok=True)
            with open(ip, "w") as f:
                f.write(impl_code)
            results.append(f"OK: Wrote impl to {ip}")
        except Exception as e:
            return f"ERROR writing impl {ip}: {e}"

        # Run pytest
        proc = subprocess.run(
            ["python", "-m", "pytest", str(tp), "-v", "--tb=short"],
            capture_output=True, text=True, timeout=30,
            cwd=str(self.workdir)
        )
        results.append(f"\n{proc.stdout}")
        if proc.stderr:
            results.append(f"\n{proc.stderr}")
        return "\n".join(results)

    def _git_commit(self, message: str, push: bool) -> str:
        import subprocess
        result = subprocess.run(
            ["git", "add", "-A"],
            capture_output=True, text=True, cwd=str(self.workdir)
        )
        result2 = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=str(self.workdir)
        )
        out = result2.stdout + result2.stderr
        if push and result2.returncode == 0:
            result3 = subprocess.run(
                ["git", "push"],
                capture_output=True, text=True, timeout=60, cwd=str(self.workdir)
            )
            out += "\n" + result3.stdout + result3.stderr
        return out or "OK: git operation completed"


# Tool definitions for LLM
TOOL_DEFINITIONS = [
    {
        "name": "bash",
        "description": "Execute a shell command and return stdout/stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {"type": "integer", "description": "Max seconds (default: 30)"}
        }, "required": ["command"]}
    },
    {
        "name": "read_file",
        "description": "Read a file and return its contents.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "Line to start from (1-indexed)"},
            "limit": {"type": "integer", "description": "Max lines to read (default: 500)"}
        }, "required": ["path"]}
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates or overwrites.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        }, "required": ["path", "content"]}
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "base_dir": {"type": "string"}
        }, "required": ["pattern"]}
    },
    {
        "name": "grep",
        "description": "Search for pattern in files.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "file_glob": {"type": "string"}
        }}
    },
    {
        "name": "apply_diff",
        "description": "Apply a unified diff to a file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "diff": {"type": "string"}
        }, "required": ["path", "diff"]}
    },
    {
        "name": "tdd_test",
        "description": "Write test + stub impl, run pytest.",
        "parameters": {"type": "object", "properties": {
            "test_path": {"type": "string"},
            "impl_path": {"type": "string"},
            "test_code": {"type": "string"},
            "impl_code": {"type": "string"}
        }, "required": ["test_path", "impl_path", "test_code", "impl_code"]}
    },
    {
        "name": "git_commit",
        "description": "Git add + commit + optional push.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"},
            "push": {"type": "boolean"}
        }, "required": ["message"]}
    },
]


def run_agent_loop(
    task: str,
    llm_client: Any,
    context: ImplementationContext,
    config: AgentLoopConfig | None = None,
    system_prompt: str | None = None,
    workdir: Path | None = None,
    tools: list[dict] | None = None,
) -> LoopResult:
    """Run the real LLM-driven closed loop.

    This is the core execution engine for RalphLoop states. It:
    1. Calls LLM with task + conversation history + tools
    2. Executes tool calls
    3. Feeds results back to LLM
    4. Repeats until LLM returns text without tool calls

    Args:
        task: The current task description.
        llm_client: LLM client instance (must have .complete() method).
        context: ImplementationContext for state tracking.
        config: Optional loop configuration.
        system_prompt: Optional system prompt.
        workdir: Working directory for file operations.
        tools: Tool definitions for the LLM (default: TOOL_DEFINITIONS).

    Returns:
        LoopResult with completion status, content, turns, tool count.
    """
    config = config or AgentLoopConfig()
    tools = tools or TOOL_DEFINITIONS
    workdir = workdir or Path.cwd()

    executor = ToolExecutor(workdir=workdir)

    if system_prompt is None:
        system_prompt = (
            "You are Ralph, an expert coding assistant following the RalphLoop methodology.\n"
            "You help users implement code using a PLAN → ACT → VERIFY → REFLECT cycle.\n"
            "Use tools to read, write, and modify files. Always prefer precise edits (apply_diff) over full rewrites.\n"
            "When done, provide a clear summary of what was accomplished."
        )

    # Initialize messages
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    # Track in context
    context.task = task
    context.context_window = config.context_window

    complete = False
    final_content = ""
    turns = 0
    total_tool_calls = 0

    for turn in range(config.max_turns):
        turns += 1

        # Call LLM
        try:
            response = llm_client.complete(
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            messages.append({
                "role": "user",
                "content": f"LLM call failed: {exc}. Please try to continue or explain the error."
            })
            continue

        # Parse response — handle both dict-style and object-style responses
        if isinstance(response, dict):
            content = response.get("content", "") or ""
            raw_tool_calls = response.get("tool_calls", []) or []
        else:
            content = getattr(response, "content", "") or ""
            raw_tool_calls = getattr(response, "tool_calls", []) or []

        # Normalize to list of dict tool calls
        tool_calls: list[dict] = []
        for tc in raw_tool_calls:
            if isinstance(tc, dict):
                tool_calls.append(tc)
            else:
                # Object with .id, .name, .input attributes
                tc_id = getattr(tc, "id", "") or getattr(tc, "tool_use_id", "") or ""
                tc_name = getattr(tc, "name", "") or ""
                tc_input = getattr(tc, "input", {}) or {}
                tool_calls.append({"id": tc_id, "name": tc_name, "args": tc_input})

        # Add assistant message to conversation
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            # Convert ToolCall objects to dicts for message format
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "args": tc.input}
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        if content:
            final_content = content

        # Stop if no tool calls and we have content
        if not tool_calls:
            if content and config.stop_on_content:
                complete = True
                break
            elif not content:
                # Empty response with no tools — might be waiting for more
                messages.append({
                    "role": "user",
                    "content": "Please continue or complete the task."
                })
                continue

        # Execute tool calls
        for tc in tool_calls:
            total_tool_calls += 1
            tc_id = tc.id
            tc_name = tc.name
            tc_args = tc.input if isinstance(tc.input, dict) else {}

            result_str = executor.execute(tc_name, tc_args)
            context.add_tool_result(tc_name, result_str, success="ERROR" not in result_str)

            # Add tool result to messages (Anthropic tool_result format)
            messages.append({
                "role": "user",
                "content": result_str,
            })

        # Check budget
        budget = context.budget_percent
        if budget >= 70.0:
            messages.append({
                "role": "user",
                "content": "Context budget nearly exhausted. Please wrap up immediately."
            })

    # Update context
    context.messages = messages

    return LoopResult(
        complete=complete,
        final_content=final_content,
        turns=turns,
        tool_calls=total_tool_calls,
        context=context,
    )
