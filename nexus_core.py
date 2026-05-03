#!/usr/bin/env python3
"""
Nexus Core — RalphLoop-driven coding agent (Tetris-Test-verified)

RalphLoop: PLAN → ACT → VERIFY → REFLECT → (COMMIT|RETRY|ESCALATE|ABORT)
"""

from __future__ import annotations
import os, sys, json, re, time, subprocess
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

# ─── LLM Client ─────────────────────────────────────────────────────────────────

def _detect_provider() -> tuple[str, str]:
    """Auto-detect the best available LLM provider from environment."""
    # Priority: Anthropic > OpenAI > Ollama (local)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", os.environ["OPENAI_API_KEY"]
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        # Edge: key exists in env but not detected above
        return "anthropic", os.environ.get("ANTHROPIC_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    # Try Ollama (local)
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        if req.status == 200:
            return "ollama", "local"
    except:
        pass
    return "none", ""


def _get_model_for_provider(provider: str, complexity: str = "medium") -> str:
    """Get the best available model for the provider."""
    if provider == "anthropic":
        # Use actual available models
        return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    elif provider == "openai":
        return os.environ.get("OPENAI_MODEL", "gpt-4o")
    elif provider == "ollama":
        return os.environ.get("OLLAMA_MODEL", "llama3")
    return "claude-sonnet-4-20250514"


class LLMClient:
    """Multi-provider LLM client with tool-calling support.
    
    Auto-detects available provider from environment variables:
    - ANTHROPIC_API_KEY → Anthropic (Claude)
    - OPENAI_API_KEY → OpenAI (GPT-4o)
    - localhost:11434 → Ollama (local)
    """
    
    def __init__(self, provider: str = "auto"):
        if provider == "auto":
            provider, _ = _detect_provider()
        self.provider = provider
        self.api_key: str
        if provider == "auto":
            provider = "anthropic"
        detected_provider, self.api_key = _detect_provider()
        if provider == "auto":
            self.provider = detected_provider
        
        if self.provider == "none":
            raise ValueError(
                "No LLM provider available. Set one of:\n"
                "  ANTHROPIC_API_KEY=sk-...  (Claude models)\n"
                "  OPENAI_API_KEY=sk-...     (GPT-4o)\n"
                "  ollama serve              (local Llama)"
            )
        
    def complete(self, messages: list[dict], tools: list[dict],
                 model: str | None = None) -> dict:
        """Send a completion request with tool-calling support.
        
        Returns: {
            "content": str,
            "tool_calls": [{"name": str, "args": dict}] 
        }
        """
        if self.provider == "anthropic":
            return self._anthropic_complete(messages, tools, model)
        elif self.provider == "openai":
            return self._openai_complete(messages, tools, model)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")
    
    def _anthropic_complete(self, messages: list[dict], tools: list[dict], model: str | None = None) -> dict:
        if model is None:
            model = _get_model_for_provider(self.provider)
        """Anthropic Messages API with tool_use."""
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        
        # Convert messages to Anthropic format
        anthropic_messages = []
        for msg in messages:
            role = msg["role"]
            if role == "user":
                anthropic_messages.append({"role": "user", "content": msg["content"]})
            elif role == "assistant":
                # Handle both text-only and tool-call content blocks
                content_blocks = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls", []):
                    content_blocks.append({
                        "type": "tool_use", 
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["args"]
                    })
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            elif role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg["tool_call_id"],
                        "content": msg["content"]
                    }]
                })
        
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=anthropic_messages,
            tools=[{"name": t["name"], "description": t.get("description",""), "input_schema": t["parameters"]} for t in tools]
        )
        
        # Parse response
        result_content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                result_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "args": block.input
                })
        
        return {"content": result_content, "tool_calls": tool_calls}

    def _openai_complete(self, messages: list[dict], tools: list[dict], model: str | None = None) -> dict:
        if model is None:
            model = _get_model_for_provider(self.provider)
        """OpenAI Chat Completions API with tools."""
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        
        oai_messages = []
        for msg in messages:
            if msg["role"] == "tool":
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg["content"]
                })
            else:
                content = msg.get("content", "")
                if msg.get("tool_calls"):
                    # Keep tool calls in the message for OAI
                    pass
                oai_messages.append({"role": msg["role"], "content": content})
        
        # Merge text + tool_calls into proper OAI format
        response = client.chat.completions.create(
            model=model,
            messages=oai_messages,
            tools=[{"type": "function", "function": {"name": t["name"], "description": t.get("description",""), "parameters": t["parameters"]}} for t in tools],
            tool_choice="auto"
        )
        
        choice = response.choices[0]
        result_content = choice.message.content or ""
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments)
                })
        
        return {"content": result_content, "tool_calls": tool_calls}


# ─── Tool Executor ─────────────────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict] = [
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
            "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
            "base_dir": {"type": "string"}
        }, "required": ["pattern"]}
    },
    {
        "name": "grep",
        "description": "Search for pattern in files.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Directory to search"},
            "file_glob": {"type": "string"}
        }}
    },
    {
        "name": "apply_diff",
        "description": "Apply a unified diff to a file. The most powerful editing tool.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "diff": {"type": "string", "description": "Unified diff string"}
        }, "required": ["path", "diff"]}
    },
    {
        "name": "tdd_test",
        "description": "Write a test file AND a simple implementation stub, then run tests.",
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


class ToolExecutor:
    """Executes tool calls and returns results."""
    
    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()
    
    def execute(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool and return its result as a string."""
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
        """Apply a unified diff to a file."""
        p = (self.workdir / path).resolve()
        if not p.exists():
            return f"ERROR: File not found: {p}"
        
        # Parse the unified diff
        import difflib
        lines = diff.split("\n")
        
        # Find the original file content
        with open(p) as f:
            original_lines = f.readlines()
        
        # Parse unified diff
        # @@ -start,count +start,count @@
        # Format: --- original\n+++ new\n@@@
        patched_lines = original_lines[:]
        diff_lines = []
        in_diff = False
        old_start, old_count, new_start = 0, 0, 0
        
        for line in lines:
            if line.startswith("@@ "):
                in_diff = True
                # Parse @@ -old_start,old_count +new_start,new_count @@
                m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if m:
                    old_start = int(m.group(1))
                    old_count = int(m.group(2)) if m.group(2) else 1
                    new_start = int(m.group(3))
                    new_count = int(m.group(4)) if m.group(4) else 1
                diff_lines = []
            elif in_diff and line.startswith("---"):
                in_diff = False
                # Process the diff
                idx = old_start - 1  # 0-indexed
                add_lines = []
                for dl in diff_lines:
                    if dl.startswith("-"):
                        # Delete
                        if idx < len(patched_lines):
                            patched_lines.pop(idx)
                    elif dl.startswith("+"):
                        # Insert
                        add_lines.append(dl[1:])
                        patched_lines.insert(idx, dl[1:])
                        idx += 1
                    elif dl.startswith(" "):
                        idx += 1
                
                # Write back
                with open(p, "w") as f:
                    f.writelines(patched_lines)
                return f"OK: Applied diff to {p} ({old_count} lines removed, {len(add_lines)} added)"
            elif in_diff:
                diff_lines.append(line)
        
        # If we get here, the diff format might be different - try line-by-line
        patched_lines = original_lines[:]
        idx = 0
        adds = []
        dels = []
        
        for line in lines:
            if line.startswith("@@"):
                continue
            elif line.startswith("-"):
                if idx < len(patched_lines):
                    dels.append((idx, patched_lines[idx]))
                    idx += 1
            elif line.startswith("+"):
                adds.append(line[1:])
            elif line.startswith(" ") or line == "":
                idx += 1
        
        # Apply deletions in reverse to not shift indices
        for idx, _ in reversed(dels):
            patched_lines.pop(idx)
        
        # Apply additions
        for i, line in enumerate(adds):
            patched_lines.insert(dels[0][0] if dels else 0 + i, line)
        
        with open(p, "w") as f:
            f.writelines(patched_lines)
        
        return f"OK: Applied diff to {p}"
    
    def _tdd_test(self, test_path: str, impl_path: str, test_code: str, impl_code: str) -> str:
        """Write test + implementation, run test."""
        workdir = self.workdir
        
        # Write test
        test_p = workdir / test_path
        test_p.parent.mkdir(parents=True, exist_ok=True)
        with open(test_p, "w") as f:
            f.write(test_code)
        
        # Write impl (stub)
        impl_p = workdir / impl_path
        impl_p.parent.mkdir(parents=True, exist_ok=True)
        with open(impl_p, "w") as f:
            f.write(impl_code)
        
        # Run test
        result = subprocess.run(
            ["python3", str(test_p)], capture_output=True, text=True,
            timeout=30, cwd=str(workdir)
        )
        
        return (f"TEST FILE: {test_p}\n"
                f"IMPL FILE: {impl_p}\n"
                f"[exit {result.returncode}]\n"
                f"STDOUT:\n{result.stdout[:2000]}\n"
                f"STDERR:\n{result.stderr[:1000]}")
    
    def _git_commit(self, message: str, push: bool) -> str:
        cmds = [
            ["git", "add", "-A"],
            ["git", "commit", "-m", message],
        ]
        if push:
            cmds.append(["git", "push"])
        
        output = []
        for cmd in cmds:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.workdir))
            output.append(f"{' '.join(cmd)} → {result.returncode}")
            if result.stdout:
                output.append(result.stdout.strip())
            if result.stderr:
                output.append(result.stderr.strip())
        return "\n".join(output)


# ─── RalphLoop State Machine ───────────────────────────────────────────────────

@dataclass
class RalphState:
    """RalphLoop state machine state."""
    name: str = "PLAN"  # PLAN | ACT | VERIFY | REFLECT | COMMIT | RETRY | ESCALATE | ABORT
    retries: int = 0
    max_retries: int = 3
    turns: int = 0
    max_turns: int = 200
    context_tokens: int = 0
    context_tier: str = "GOOD"  # PEAK | GOOD | DEGRADING | POOR
    checkpoints: list[dict] = field(default_factory=list)
    error: str | None = None
    last_tool_results: list[str] = field(default_factory=list)
    done: bool = False
    
    def transition(self, new_state: str, error: str | None = None) -> None:
        print(f"[RalphLoop] {self.name} → {new_state}" + (f" | {error}" if error else ""))
        self.name = new_state
        self.error = error
        if new_state == "RETRY":
            self.retries += 1
        elif new_state in ("PLAN", "COMMIT"):
            self.retries = 0


# ─── RalphLoop ─────────────────────────────────────────────────────────────────

class RalphLoop:
    """
    The core orchestration loop.
    
    PLAN: Analyze task, decide approach
    ACT: Execute via LLM + tools
    VERIFY: Check if output is correct
    REFLECT: Determine next step
    COMMIT/RETRY/ESCALATE/ABORT: Final dispositions
    """
    
    SYSTEM_PROMPT = """You are Nexus, a senior software engineer with deep expertise in all programming languages and frameworks. You are methodical, precise, and produce production-quality code.

## Your RalphLoop Process

You operate in a strict state machine:

1. **PLAN**: Before writing ANY code, understand the task fully. Read existing files, understand the codebase structure. Ask: what's the minimal correct implementation?

2. **ACT**: Execute your plan using tools. Write files, run commands, test. Be precise.

3. **VERIFY**: After each significant action, verify the output. Run tests, check file contents.

4. **REFLECT**: After each turn, decide: is the task done? Should I retry? Should I escalate?

## Tool Philosophy

You have these tools available:
- **bash**: Run shell commands (git, pytest, ls, etc.)
- **read_file**: Read any file with line numbers
- **write_file**: Create or overwrite a file
- **apply_diff**: Apply a unified diff to an existing file (most precise editing)
- **glob**: Find files matching a pattern
- **grep**: Search file contents
- **tdd_test**: Write test + stub implementation, run test
- **git_commit**: Save your work

## TDD Enforcement

For ANY code task, you MUST follow TDD:
1. Write the failing test FIRST
2. Verify test fails
3. Write minimal implementation to pass
4. Verify test passes
5. Refactor

## Quality Bar

- All tests must pass before commit
- No hardcoded secrets
- Clean code with no TODO comments in final output
- Files must be syntactically valid

## Output Format

When responding, be concise but complete. Show your reasoning briefly, then act.
When you need to use a tool, do so immediately. Don't ask for permission.

Remember: You are a SENIOR ENGINEER. Write code you'd be proud to merge.
"""

    def __init__(self, workdir: Path | None = None, model: str = "sonnet"):
        self.workdir = workdir or Path.cwd()
        self.model = model  # "sonnet" | "opus" | "haiku"
        self.llm = LLMClient(provider="anthropic")
        self.tools = ToolExecutor(workdir=self.workdir)
        self.state = RalphState()
        self.messages: list[dict] = []
        self.tool_defs = TOOL_DEFINITIONS
        self._setup_system_prompt()
    
    def _setup_system_prompt(self):
        self.messages = [
            {"role": "user", "content": self.SYSTEM_PROMPT}
        ]
    
    def run(self, task: str) -> dict[str, Any]:
        """Main entry point. Run a task through RalphLoop."""
        print(f"\n{'='*60}")
        print(f"NEXUS RalphLoop starting | Task: {task[:60]}...")
        print(f"{'='*60}")
        
        self.state = RalphState()
        self.state.transition("PLAN")
        
        # Initial task message
        self.messages.append({"role": "user", "content": f"TASK: {task}\n\nWork directory: {self.workdir}"})
        
        while not self.state.done and self.state.turns < self.state.max_turns:
            self.state.turns += 1
            print(f"\n--- Turn {self.state.turns} | State: {self.state.name} ---")
            
            if self.state.name == "PLAN":
                self._plan()
            elif self.state.name == "ACT":
                self._act()
            elif self.state.name == "VERIFY":
                self._verify()
            elif self.state.name == "REFLECT":
                self._reflect()
            elif self.state.name == "RETRY":
                self._retry()
            elif self.state.name in ("COMMIT", "ABORT", "ESCALATE"):
                break
        
        return self._summarize()
    
    def _plan(self) -> None:
        """PLAN: LLM analyzes task and decides approach."""
        # Check if this is a coding task that needs TDD
        task_lower = self.messages[-1]["content"].lower()
        if any(kw in task_lower for kw in ["implement", "write code", "build", "create", "add feature"]):
            plan_msg = (
                "\n\nPLAN YOUR APPROACH:\n"
                "1. What files need to be created/modified?\n"
                "2. What's the TDD approach (test first)?\n"
                "3. What tools will you use?\n"
                "Respond briefly, then immediately start with ACT phase using tools."
            )
            self.messages.append({"role": "user", "content": plan_msg})
        else:
            self.messages.append({"role": "user", "content": "\nAnalyze this task and take action. What do you need to do first?"})
        
        response = self.llm.complete(self.messages, self.tool_defs, model=self._model_for_complexity())
        self._process_response(response)
    
    def _act(self) -> None:
        """ACT: Execute tool calls until none remain."""
        self.state.transition("ACT")
        
        response = self.llm.complete(self.messages, self.tool_defs, model=self._model_for_complexity())
        self._process_response(response)
    
    def _process_response(self, response: dict) -> None:
        """Process LLM response: add to messages, execute tool calls."""
        content = response.get("content", "")
        tool_calls = response.get("tool_calls", [])
        
        # Add assistant message
        assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        self.messages.append(assistant_msg)
        
        if content:
            print(f"\n[Nexus] {content[:300]}" + ("..." if len(content) > 300 else ""))
        
        # Execute tool calls
        for tc in tool_calls:
            tc_id = tc["id"]
            tc_name = tc["name"]
            tc_args = tc["args"]
            
            print(f"\n[Tool] {tc_name}({json.dumps(tc_args, ensure_ascii=False)[:100]})")
            result = self.tools.execute(tc_name, tc_args)
            self.state.last_tool_results.append(result)
            
            # Add tool result to messages
            self.messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result
            })
            print(f"[Result] {result[:200]}" + ("..." if len(result) > 200 else ""))
        
        # Move to next state
        if tool_calls:
            self.state.transition("VERIFY")
        elif content:
            self.state.transition("REFLECT")
    
    def _verify(self) -> None:
        """VERIFY: Check if the last tool results are satisfactory."""
        self.state.transition("VERIFY")
        
        last_results = self.state.last_tool_results[-3:] if self.state.last_tool_results else []
        verify_msg = (
            "\n\nVERIFY PHASE:\n"
            f"Recent tool results:\n" + "\n---\n".join(last_results) + "\n\n"
            "Check:\n"
            "1. Did the tools succeed (no ERROR in output)?\n"
            "2. Are files syntactically valid?\n"
            "3. If tests ran, did they pass?\n\n"
            "Respond with your verification assessment, then take any needed corrective action."
        )
        self.messages.append({"role": "user", "content": verify_msg})
        
        response = self.llm.complete(self.messages, self.tool_defs, model=self._model_for_complexity())
        self._process_response(response)
    
    def _reflect(self) -> None:
        """REFLECT: Decide if task is done or needs more work."""
        self.state.transition("REFLECT")
        
        reflect_msg = (
            "\n\nREFLECT PHASE:\n"
            "Ask yourself:\n"
            "1. Is the task complete (code written, tests passing, committed)?\n"
            "2. Should I continue ACTing or VERIFYing?\n"
            "3. Should I transition to COMMIT / RETRY / ESCALATE / ABORT?\n\n"
            "Respond with a brief reflection, then take the appropriate action."
        )
        self.messages.append({"role": "user", "content": reflect_msg})
        
        response = self.llm.complete(self.messages, self.tool_defs, model="sonnet")
        self._process_response(response)
        
        # Check if done
        if "task complete" in response.get("content", "").lower() or "commit" in response.get("content", "").lower():
            self.state.transition("COMMIT")
            self._commit()
    
    def _retry(self) -> None:
        """RETRY: Attempt to fix a failure."""
        self.state.transition("RETRY", error=self.state.error)
        
        if self.state.retries > self.state.max_retries:
            print(f"\n[!!] Max retries ({self.state.max_retries}) exceeded!")
            self.state.transition("ESCALATE")
            return
        
        retry_msg = (
            f"\n\nRETRY PHASE (attempt {self.state.retries}/{self.state.max_retries}):\n"
            f"Error: {self.state.error}\n\n"
            "Analyze the failure and create a corrected plan. "
            "What went wrong? What will you do differently?"
        )
        self.messages.append({"role": "user", "content": retry_msg})
        
        response = self.llm.complete(self.messages, self.tool_defs, model="sonnet")
        self._process_response(response)
    
    def _commit(self) -> None:
        """COMMIT: Save work with git."""
        self.state.transition("COMMIT")
        
        commit_msg = (
            "\n\nCOMMIT PHASE:\n"
            "Do a final check, then commit your work with a meaningful message.\n"
            "Use bash to run: git add -A && git commit -m 'message'\n"
            "If there are no changes to commit, just say so."
        )
        self.messages.append({"role": "user", "content": commit_msg})
        
        response = self.llm.complete(self.messages, self.tool_defs, model="sonnet")
        self._process_response(response)
        
        self.state.done = True
    
    def _summarize(self) -> dict[str, Any]:
        """Summarize the run."""
        summary = {
            "turns": self.state.turns,
            "final_state": self.state.name,
            "retries": self.state.retries,
            "done": self.state.done,
        }
        print(f"\n{'='*60}")
        print(f"NEXUS RalphLoop finished | {self.state.turns} turns | Final: {self.state.name}")
        print(f"{'='*60}")
        return summary
    
    def _model_for_complexity(self) -> str:
        """Route to appropriate model based on state and provider."""
        if self.state.turns <= 2 and self.state.name in ("PLAN", "ACT"):
            complexity = "simple"
        else:
            complexity = "complex"
        return _get_model_for_provider(self.provider, complexity)


# ─── Nexus Main ────────────────────────────────────────────────────────────────

class Nexus:
    """
    The main Nexus agent. 
    
    Usage:
        nexus = Nexus(workdir=Path("/path/to/project"))
        result = nexus.run("Implement user authentication")
    """
    
    def __init__(self, workdir: Path | str | None = None, model: str = "sonnet"):
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.model = model
        self.loop = RalphLoop(workdir=self.workdir, model=model)
    
    def run(self, task: str) -> dict[str, Any]:
        """Run a task through RalphLoop."""
        return self.loop.run(task)
    
    def chat(self, message: str) -> dict[str, Any]:
        """Send a message in interactive chat mode."""
        return self.loop.run(message)


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Nexus — RalphLoop Coding Agent")
    parser.add_argument("task", nargs="?", help="Task description")
    parser.add_argument("--workdir", "-C", default=".", help="Working directory")
    parser.add_argument("--model", "-m", default="sonnet", choices=["sonnet", "opus", "haiku"])
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--chat", action="store_true", help="Chat mode")
    
    args = parser.parse_args()
    
    workdir = Path(args.workdir).expanduser().resolve()
    nexus = Nexus(workdir=workdir, model=args.model)
    
    if args.interactive or args.chat or not args.task:
        print("Nexus Interactive Mode — type 'exit' to quit")
        print(f"Workdir: {workdir}")
        while True:
            try:
                task = input("\n> ")
                if task.strip().lower() in ("exit", "quit", "q"):
                    break
                if not task.strip():
                    continue
                nexus.run(task)
            except EOFError:
                break
    else:
        result = nexus.run(args.task)
        print(f"\nResult: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
