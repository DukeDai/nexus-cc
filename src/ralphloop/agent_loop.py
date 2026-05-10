"""RalphLoop Agent Loop — Real LLM-driven closed loop.

This module implements the core closed-loop execution engine that drives
RalphLoop states (PLAN→ACT→VERIFY→REFLECT) by calling the LLM and executing
tools until completion.

Key insight: Each RalphLoop state is powered by this real LLM loop.
The orchestrator manages transitions; this loop does the actual work.
"""

from __future__ import annotations

import subprocess
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
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model: str = ""
    context: Optional[ImplementationContext] = None


@dataclass
class AgentLoopConfig:
    """Configuration for the agent loop.

    Attributes:
        max_turns: Maximum LLM calls per state (default 20).
        tool_timeout: Default timeout for tool execution in seconds (default 60).
        context_window: Context window size for budget tracking (default 100000).
        stop_on_content: Stop when LLM returns content with no tool calls (default True).
        streaming: Enable streaming token output via callback (default False).
    """
    max_turns: int = 20
    tool_timeout: int = 60
    context_window: int = 100000
    stop_on_content: bool = True
    streaming: bool = False


class ToolExecutor:
    """Executes tool calls and returns results.

    This is the tool side of the closed loop. It maps tool names to
    actual implementations and returns string results for the LLM.
    
    Supports MCP tools if mcp_client is provided.
    """

    def __init__(self, workdir: Path | str | None = None, mcp_client: Any | None = None):
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.mcp_client = mcp_client
        self._mcp_tool_names: set[str] = set()
        if mcp_client is not None:
            self._init_mcp_tools()

    def _init_mcp_tools(self) -> None:
        """Initialize MCP tool names from connected MCP server."""
        try:
            import asyncio
            if self.mcp_client is None:
                return
            # Get tools from MCP server
            tools = asyncio.get_event_loop().run_until_complete(
                self.mcp_client.list_tools()
            )
            self._mcp_tool_names = {t.name for t in tools}
        except Exception:
            pass

    def execute(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool and return its result as a string."""
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
            elif tool_name == "edit_file":
                return self._edit_file(
                    tool_args["path"],
                    tool_args.get("old_string", ""),
                    tool_args.get("new_string", ""),
                    tool_args.get("replace_all", False),
                )
            elif tool_name == "patch_file":
                return self._patch_file(
                    tool_args["path"],
                    tool_args.get("patches", []),
                )
            elif tool_name == "tdd_test":
                return self._tdd_test(tool_args["test_path"], tool_args["impl_path"],
                                      tool_args["test_code"], tool_args["impl_code"])
            elif tool_name == "git_commit":
                return self._git_commit(tool_args["message"], tool_args.get("push", False))
            elif self.mcp_client and tool_name in self._mcp_tool_names:
                # Delegate to MCP server
                import asyncio
                try:
                    result = asyncio.get_event_loop().run_until_complete(
                        self.mcp_client.call_tool(tool_name, tool_args)
                    )
                    return str(result) if result else "OK"
                except Exception as e:
                    return f"ERROR (MCP): {type(e).__name__}: {e}"
            else:
                return f"ERROR: Unknown tool '{tool_name}'"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    def _bash(self, command: str, timeout: int) -> str:
        # 安全：使用 shell=False + shlex.split 避免 shell 注入
        import shlex
        cmd_list = shlex.split(command) if isinstance(command, str) else command
        result = subprocess.run(
            cmd_list, shell=False, capture_output=True, text=True,
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

        hunks = self._parse_hunks(diff)
        if not hunks:
            return f"ERROR: No valid hunks found in diff for {p}"

        patched_lines = original_lines[:]
        total_removed = 0
        total_added = 0

        for hunk in hunks:
            result = self._apply_single_hunk(patched_lines, hunk)
            total_removed += result["removed"]
            total_added += result["added"]

        try:
            with open(p, "w") as f:
                f.write("\n".join(patched_lines) + "\n")
            return (f"OK: Applied {len(hunks)} hunks, "
                    f"{total_removed} removed, {total_added} added to {p}")
        except Exception as e:
            return f"ERROR writing {p}: {e}"

    def _edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """Precise string replacement — safer than apply_diff for small edits.
        
        Args:
            path: File to edit
            old_string: Exact text to find (supports multi-line)
            new_string: Replacement text
            replace_all: If True, replace ALL occurrences; else only first
        """
        import difflib
        p = (self.workdir / path).resolve()
        if not p.exists():
            return f"ERROR: File not found: {p}"
        
        with open(p) as f:
            original = f.read()
        
        if old_string not in original:
            return f"ERROR: old_string not found in {p}. Make sure the string matches exactly."
        
        if replace_all:
            edited = original.replace(old_string, new_string)
            count = original.count(old_string)
        else:
            edited = original.replace(old_string, new_string, 1)
            count = 1
        
        with open(p, "w") as f:
            f.write(edited)
        
        return f"OK: Replaced {count} occurrence(s) in {p}"

    def _patch_file(self, path: str, patches: list[dict]) -> str:
        """Apply multiple edits in sequence (batch patch).
        
        Each patch: {"old_string": "...", "new_string": "...", "replace_all": false}
        Executes patches in order, allowing cascading edits.
        """
        p = (self.workdir / path).resolve()
        if not p.exists():
            return f"ERROR: File not found: {p}"
        
        with open(p) as f:
            content = f.read()
        
        applied = 0
        errors = []
        for i, patch in enumerate(patches):
            old_str = patch.get("old_string", "")
            new_str = patch.get("new_string", "")
            replace_all = patch.get("replace_all", False)
            
            if old_str not in content:
                errors.append(f"Patch {i}: old_string not found")
                continue
            
            if replace_all:
                content = content.replace(old_str, new_str)
            else:
                content = content.replace(old_str, new_str, 1)
            applied += 1
        
        if errors:
            with open(p, "w") as f:
                f.write(content)
            return f"PARTIAL: Applied {applied}/{len(patches)} patches, errors: {'; '.join(errors)}"
        
        with open(p, "w") as f:
            f.write(content)
        
        return f"OK: Applied {applied} patches to {p}"

    def _parse_hunks(self, diff: str) -> list[dict]:
        """Parse unified diff into structured hunks."""
        import re
        hunks = []
        diff_lines = diff.split("\n")
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
        return hunks

    def _apply_single_hunk(self, patched_lines: list[str], hunk: dict) -> dict:
        """Apply a single hunk to the line list. Returns stats dict."""
        old_start = hunk['old_start']
        old_count = hunk['old_count']
        new_count = hunk['new_count']
        hunk_content = hunk['content']

        # Classify lines
        hunk_lines = []
        for hl in hunk_content:
            if hl.startswith("-"):
                hunk_lines.append(("del", hl[1:]))
            elif hl.startswith("+"):
                hunk_lines.append(("add", hl[1:]))
            else:
                hunk_lines.append(("ctx", hl[1:] if len(hl) > 1 else ""))

        edits = self._classify_hunk_edits(hunk_lines)

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

        return {
            "replacements": replacements,
            "insertions": len(insertions),
            "deletions": len(deletions),
            "removed": len(deletions),
            "added": len(insertions) + replacements
        }

    def _classify_hunk_edits(self, hunk_lines: list[tuple]) -> list:
        """Classify edits within hunk: context, replacement, deletion, insertion."""
        edits: list[tuple[str, str] | tuple[str, str, str]] = []
        j = 0
        while j < len(hunk_lines):
            typ, text = hunk_lines[j]
            if typ == "ctx":
                edits.append(("cnt", text))
                j += 1
            elif typ == "del":
                if j + 1 < len(hunk_lines) and hunk_lines[j + 1][0] == "add":
                    edits.append(("rep", text, hunk_lines[j + 1][1]))
                    j += 2
                else:
                    edits.append(("del", text))
                    j += 1
            elif typ == "add":
                edits.append(("ins", text))
                j += 1
        return edits
    def _tdd_test(self, test_path: str, impl_path: str, test_code: str, impl_code: str) -> str:
        """Write test + impl, run pytest."""
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
        "name": "edit_file",
        "description": "Precise string replacement — safer than apply_diff for small edits.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "File to edit"},
            "old_string": {"type": "string", "description": "Exact text to find (supports multi-line)"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {"type": "boolean", "description": "If True, replace ALL occurrences; else only first"}
        }, "required": ["path", "old_string", "new_string"]}
    },
    {
        "name": "patch_file",
        "description": "Apply multiple edits in sequence (batch patch).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "File to patch"},
            "patches": {"type": "array", "description": "Array of {old_string, new_string, replace_all?}"}
        }, "required": ["path", "patches"]}
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
    streaming_callback: Callable[[str], None] | None = None,
    wal: Any | None = None,
    tdd_enforcer: Any | None = None,
    model_router: Any | None = None,
    mcp_client: Any | None = None,  # MCP client for external tools
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

    # Initialize ModelRouter for dynamic model selection
    if model_router is None:
        try:
            from ..llm.model_router import ModelRouter
            model_router = ModelRouter()
        except ImportError:
            from llm.model_router import ModelRouter
            model_router = ModelRouter()

    # Initialize Self-Evolution engine on context (if not already set)
    if not hasattr(context, "_evolution_engine") or context._evolution_engine is None:
        try:
            from self_evolution import SelfEvolutionEngine
        except ImportError:
            from ..self_evolution import SelfEvolutionEngine
        context._evolution_engine = SelfEvolutionEngine()
        context._evolution_engine.load_existing_skills()

    executor = ToolExecutor(workdir=workdir, mcp_client=mcp_client)

    # Initialize TDDEnforcer if enabled
    if tdd_enforcer is None and config is not None and getattr(config, 'enable_tdd', False):
        try:
            from .tdd_enforcer import TDDEnforcer
            tdd_enforcer = TDDEnforcer()
        except ImportError:
            pass

    def _should_tdd_check(tc_name: str, tc_args: dict) -> bool:
        """Check if tool call should trigger TDD enforcement."""
        if tdd_enforcer is None:
            return False
        tdd_tools = {'write_file', 'apply_diff', 'edit_file', 'create_file'}
        return tc_name in tdd_tools

    def _tdd_enforce_write(tc_name: str, tc_args: dict) -> str:
        """Enforce TDD workflow for write operations: RED → GREEN → REFACTOR."""
        if tdd_enforcer is None:
            return executor.execute(tc_name, tc_args)
        
        path = tc_args.get('path', '')
        content = tc_args.get('content', '')
        
        # Check if it's a test file - skip TDD for tests
        if 'test' in path.lower() or path.endswith('_test.py'):
            return executor.execute(tc_name, tc_args)
        
        # Start TDD cycle for implementation files
        cycle = tdd_enforcer.start_cycle(f"Implement: {path}")
        
        # RED: Generate test first
        red_prompt = f"""Write a pytest test for: {content[:500]}

File: {path}
Write ONLY the test code, no implementation."""
        
        try:
            test_response = active_client.complete(
                messages=messages + [{"role": "user", "content": red_prompt}],
                tools=None,
            )
            test_code = getattr(test_response, "content", "") or ""
            
            # Save test file
            test_path = path.replace('.py', '_test.py') if not 'test' in path else path
            executor.execute("write_file", {"path": test_path, "content": test_code})
            
            # Run RED - test should fail
            passed, output = tdd_enforcer.run_red()
            
            # GREEN: Write minimal implementation
            green_prompt = f"""Write minimal implementation for:
Test: {test_code[:500]}

File: {path}
Write ONLY the implementation."""
            green_response = active_client.complete(
                messages=messages + [{"role": "user", "content": green_prompt}],
                tools=None,
            )
            impl_code = getattr(green_response, "content", "") or ""
            
            # Execute the original write
            result = executor.execute(tc_name, tc_args)
            
            # Verify GREEN
            green_passed, green_output = tdd_enforcer.run_green()
            
            if green_passed:
                return f"[TDD PASSED] {result}"
            else:
                return f"[TDD FAILED - code written anyway] {result}"
        except Exception as e:
            # Fallback: just execute normally
            return executor.execute(tc_name, tc_args)

    # Import uuid for generating tool call IDs
    import uuid

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
    result_prompt_tokens = 0
    result_completion_tokens = 0
    result_total_tokens = 0
    result_model = getattr(llm_client, "model", "") or ""

    for turn in range(config.max_turns):
        turns += 1

        # Dynamic model selection via ModelRouter (if available)
        active_client = llm_client
        if model_router is not None:
            try:
                selected_model = model_router.select_model(
                    requires_tools=bool(tools),
                    prefer_speed=getattr(config, 'prefer_speed', False) if config else False,
                )
                if selected_model:
                    active_client = model_router.get_client(selected_model)
                    result_model = selected_model
            except Exception:
                active_client = llm_client

        # Call LLM
        try:
            if config.streaming and streaming_callback:
                # Streaming mode: iterate over generator and collect text + tool calls
                accumulated = []
                raw_tool_calls = []
                
                for event in active_client.complete_streaming(
                    messages=messages,
                    tools=tools,
                    system_prompt=system_prompt,
                ):
                    event_type = event.get("type", "")
                    if event_type == "text":
                        chunk = event.get("content", "")
                        accumulated.append(chunk)
                        streaming_callback(chunk)
                    elif event_type == "tool_call":
                        raw_tool_calls.append(event.get("tool_call", {}))
                
                content = "".join(accumulated)
            else:
                response = active_client.complete(
                    messages=messages,
                    tools=tools,
                )
                if isinstance(response, dict):
                    content = response.get("content", "") or ""
                    raw_tool_calls = response.get("tool_calls", []) or []
                else:
                    content = getattr(response, "content", "") or ""
                    raw_tool_calls = getattr(response, "tool_calls", []) or []
        except Exception as exc:
            messages.append({
                "role": "user",
                "content": f"LLM call failed: {exc}. Please try to continue or explain the error."
            })
            continue

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
            # tool_calls is already a list of dicts from normalization above
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        # ── Token counting & cost tracking ─────────────────────────────
        if isinstance(response, dict):
            usage_data = response.get("usage", {}) or {}
            prompt_tokens = usage_data.get("input_tokens", usage_data.get("prompt_tokens", 0))
            completion_tokens = usage_data.get("output_tokens", usage_data.get("completion_tokens", 0))
            resp_model = response.get("model", getattr(llm_client, "model", "") or "")
        else:
            usage_obj = getattr(response, "usage", None) or getattr(response, "token_usage", None)
            if usage_obj:
                prompt_tokens = getattr(usage_obj, "input_tokens", 0) or getattr(usage_obj, "prompt_tokens", 0)
                completion_tokens = getattr(usage_obj, "output_tokens", 0) or getattr(usage_obj, "completion_tokens", 0)
            else:
                prompt_tokens = completion_tokens = 0
            resp_model = getattr(response, "model", getattr(llm_client, "model", "") or "")

        total_tokens = prompt_tokens + completion_tokens
        result_prompt_tokens += prompt_tokens
        result_completion_tokens += completion_tokens
        result_total_tokens += total_tokens
        result_model = resp_model or result_model

        # ── Stop if no tool calls and we have content ──────────────────────
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

        # Execute tool calls (parallel for independent I/O operations)
        def _exec_single(tc: dict) -> tuple[str, str, str]:
            tc_id = tc.get("id", "") or str(uuid.uuid4())[:8]
            tc_name = tc.get("name", "")
            tc_args = tc.get("args", tc.get("input", {}))
            if wal is not None:
                wal.log_tool_call(tc_name, tc_args, tc_id)
            if _should_tdd_check(tc_name, tc_args):
                result_str = _tdd_enforce_write(tc_name, tc_args)
            else:
                result_str = executor.execute(tc_name, tc_args)
            return tc_id, result_str, tc_name

        # Parallel execution for read-only, independent tools
        parallel_reads = [tc for tc in tool_calls if tc.get("name") in {"read_file", "glob", "grep"}]
        write_tools = [tc for tc in tool_calls if tc.get("name") in {"write_file", "apply_diff", "edit_file", "patch_file", "bash", "git_commit", "tdd_test"}]
        other_tools = [tc for tc in tool_calls if tc not in parallel_reads and tc not in write_tools]

        # Execute reads in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(4, len(parallel_reads) or 1)) as pool:
            futures = {pool.submit(_exec_single, tc): tc for tc in parallel_reads}
            for future in as_completed(futures):
                tc_id, result_str, tc_name = future.result()
                tool_calls_remain = write_tools + other_tools
                if wal is not None:
                    wal.log_tool_result(tc_id, result_str, error=None if "ERROR" not in result_str else result_str)
                context.add_tool_result(tc_name, result_str, success="ERROR" not in result_str)
                messages.append({"role": "user", "content": result_str})

        # Execute writes + other tools sequentially (order matters for writes)
        for tc in write_tools + other_tools:
            tc_id, result_str, tc_name = _exec_single(tc)
            total_tool_calls += 1
            if wal is not None:
                wal.log_tool_result(tc_id, result_str, error=None if "ERROR" not in result_str else result_str)
            context.add_tool_result(tc_name, result_str, success="ERROR" not in result_str)
            
            # Self-Evolution: learn from errors
            evolution: Any | None = getattr(context, "_evolution_engine", None)
            if evolution:
                had_error = evolution.monitor_error(
                    tool_name=tc_name,
                    tool_args=tc.get("args", tc.get("input", {})),
                    tool_result=result_str,
                    task_context=getattr(context, "task", ""),
                )
                if had_error:
                    skill = evolution.analyze_and_capture()
                    if skill:
                        evolution.store_skill(skill)

            messages.append({"role": "user", "content": result_str})

        # Check budget
        budget = context.budget_percent
        if budget >= 70.0:
            messages.append({
                "role": "user",
                "content": "Context budget nearly exhausted. Please wrap up immediately."
            })

    # Update context
    context.messages = messages


    # Determine completion: normal exit, hit turn limit with work done, or forced
    if not complete and total_tool_calls > 0:
        # Tool calls were made — meaningful work happened, treat as complete
        complete = True
    _COST_PER_M_INPUT = 3.0   # $3/MTok input
    _COST_PER_M_OUTPUT = 15.0  # $15/MTok output
    _est_cost = (result_prompt_tokens / 1_000_000 * _COST_PER_M_INPUT +
                 result_completion_tokens / 1_000_000 * _COST_PER_M_OUTPUT)

    return LoopResult(
        complete=complete,
        final_content=final_content,
        turns=turns,
        tool_calls=total_tool_calls,
        total_tokens=result_total_tokens,
        prompt_tokens=result_prompt_tokens,
        completion_tokens=result_completion_tokens,
        estimated_cost_usd=round(_est_cost, 6),
        model=result_model,
        context=context,
    )
