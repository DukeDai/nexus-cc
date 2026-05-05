"""Self-Evolution Engine — Learn from failures, capture as reusable skills.

The SelfEvolutionEngine watches for errors and failures during agent execution,
generates recovery patterns, and saves them as skills for future reuse.

Key insight (differentiator from Claude Code):
    Claude Code has no memory of past failures across sessions.
    Nexus learns: error → root cause → recovery pattern → skill.
    Next time similar error occurs, it applies the learned fix instantly.

Architecture:
    Monitor: Watches tool results and LLM responses for failure signals
    Analyzer: Determines root cause and recovery strategy
    Capturer: Generates skill markdown from error context
    Store: Saves skill to ~/.hermes/skills/ for permanent reuse
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = __import__("logging").getLogger(__name__)


# ─── Error Patterns & Recovery Strategies ───────────────────────────────────

KNOWN_ERROR_PATTERNS: list[dict] = [
    {
        "pattern": r"ModuleNotFoundError|No module named",
        "cause": "Missing Python dependency",
        "fix": "pip install {module}",
        "skill_template": "python-missing-dep",
    },
    {
        "pattern": r"SyntaxError:|IndentationError:",
        "cause": "Python syntax error in generated code",
        "fix": "Review and fix syntax before execution",
        "skill_template": "python-syntax-error",
    },
    {
        "pattern": r"FileNotFoundError:",
        "cause": "Referenced file doesn't exist",
        "fix": "Check file exists before operation, create parent dirs",
        "skill_template": "file-not-found",
    },
    {
        "pattern": r"Permission denied",
        "cause": "File permission issue",
        "fix": "Check file permissions, use chmod if needed",
        "skill_template": "permission-denied",
    },
    {
        "pattern": r"timeout|Timeout|timed out",
        "cause": "Operation took too long",
        "fix": "Increase timeout or optimize operation",
        "skill_template": "operation-timeout",
    },
    {
        "pattern": r"git.*conflict|merge conflict",
        "cause": "Git merge conflict",
        "fix": "Resolve conflict markers, then git add + commit",
        "skill_template": "git-conflict",
    },
    {
        "pattern": r"API.*(failed|error|400|401|403|404|500)",
        "cause": "API request failure",
        "fix": "Check API key, endpoint URL, request format",
        "skill_template": "api-error",
    },
    {
        "pattern": r"rate limit|RateLimitError",
        "cause": "API rate limit exceeded",
        "fix": "Add delay between requests, use exponential backoff",
        "skill_template": "rate-limit",
    },
    {
        "pattern": r"context.*exhausted|budget.*exceeded",
        "cause": "Context window nearly full",
        "fix": "Summarize conversation, continue with key context",
        "skill_template": "context-exhausted",
    },
]


@dataclass
class ErrorEvent:
    """A captured error event from agent execution."""
    timestamp: str
    error_message: str
    tool_name: str
    tool_args: dict
    tool_result: str
    task_context: str
    recovery_used: Optional[str] = None
    recovery_succeeded: Optional[bool] = None


@dataclass
class LearnedSkill:
    """A skill learned from error recovery."""
    name: str
    description: str
    trigger: str  # Error pattern that activates this skill
    root_cause: str
    recovery_steps: list[str]
    verification: str  # How to verify the fix worked
    error_examples: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0


class SelfEvolutionEngine:
    """Learn from errors, generate recovery skills, persist for future reuse.

    Usage:
        engine = SelfEvolutionEngine()
        engine.monitor_error(
            tool_name="bash",
            tool_args={"command": "python script.py"},
            tool_result="ModuleNotFoundError: No module named 'requests'",
            task_context="Implementing HTTP client",
        )
        skill = engine.analyze_and_capture()
        if skill:
            engine.store_skill(skill)
    """

    def __init__(
        self,
        skills_dir: Path | None = None,
        error_log_path: Path | None = None,
    ):
        self.skills_dir = skills_dir or (Path.home() / ".hermes" / "skills")
        self.error_log_path = error_log_path or (Path.home() / ".nexus" / "error_log.jsonl")
        self._current_event: ErrorEvent | None = None
        self._skills_cache: dict[str, LearnedSkill] = {}

        # Ensure directories exist
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.error_log_path.parent.mkdir(parents=True, exist_ok=True)

    def monitor_error(
        self,
        tool_name: str,
        tool_args: dict,
        tool_result: str,
        task_context: str = "",
        recovery_used: str | None = None,
    ) -> bool:
        """Detect if tool_result contains an error. Returns True if error detected."""
        if not self._is_error(tool_result):
            return False

        self._current_event = ErrorEvent(
            timestamp=datetime.utcnow().isoformat(),
            error_message=self._extract_error_message(tool_result),
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            task_context=task_context,
            recovery_used=recovery_used,
        )

        # Log the error
        self._log_error(self._current_event)
        logger.info(f"[SelfEvolution] Error detected: {self._current_event.error_message[:80]}")
        return True

    def update_recovery_result(self, succeeded: bool, recovery_used: str) -> None:
        """Update the current event with recovery outcome."""
        if self._current_event:
            self._current_event.recovery_used = recovery_used
            self._current_event.recovery_succeeded = succeeded

    def analyze_and_capture(self) -> LearnedSkill | None:
        """Analyze current error event and generate a learned skill if warranted."""
        if not self._current_event:
            return None

        event = self._current_event
        error_msg = event.error_message

        # Find matching pattern
        matched_pattern = None
        for kp in KNOWN_ERROR_PATTERNS:
            if re.search(kp["pattern"], error_msg, re.IGNORECASE):
                matched_pattern = kp
                break

        if not matched_pattern:
            # Try generic fallback — derive pattern from error message
            error_keywords = re.findall(r'[A-Z][A-Za-z]+', error_msg[:100])
            pattern = '|'.join(re.escape(k) for k in error_keywords[:5]) or re.escape(error_msg[:50])
            matched_pattern = {
                "pattern": pattern,
                "cause": "Unknown error",
                "fix": "Investigate error message and determine root cause",
                "skill_template": "generic-error",
            }

        # Check if we already have a similar skill
        skill_id = self._skill_id_for_error(error_msg)
        if skill_id in self._skills_cache:
            # Increment counters
            cached = self._skills_cache[skill_id]
            if event.recovery_succeeded:
                cached.success_count += 1
            else:
                cached.failure_count += 1
            self._save_skill_metadata(cached)
            return None  # Already learned

        # Generate new skill
        skill = LearnedSkill(
            name=f"recover-{skill_id}",
            description=(
                f"Recovery skill for: {matched_pattern.get('cause', 'Unknown error')}. "
                f"Error: {error_msg[:100]}"
            ),
            trigger=matched_pattern["pattern"],
            root_cause=matched_pattern.get("cause", "Unknown"),
            recovery_steps=self._generate_recovery_steps(event, matched_pattern),
            verification="Verify the original operation succeeds without error.",
            error_examples=[error_msg],
        )

        return skill

    def store_skill(self, skill: LearnedSkill) -> Path:
        """Save a learned skill as a SKILL.md file."""
        skill_path = self.skills_dir / f"{skill.name}.md"
        content = self._render_skill_md(skill)
        skill_path.write_text(content)
        self._skills_cache[skill.name] = skill
        logger.info(f"[SelfEvolution] Skill stored: {skill_path}")
        return skill_path

    def get_relevant_skills(self, error_message: str) -> list[LearnedSkill]:
        """Get skills relevant to a given error message."""
        relevant = []
        for skill in self._skills_cache.values():
            if re.search(skill.trigger, error_message, re.IGNORECASE):
                relevant.append(skill)
        return relevant

    def get_best_recovery(self, error_message: str) -> str | None:
        """Get the most successful recovery for an error pattern.
        
        Even if no successful recoveries are recorded yet, return the learned
        recovery steps (the agent has already invested in learning them).
        """
        skills = self.get_relevant_skills(error_message)
        if not skills:
            return None

        # Sort by success rate (successful/total), fall back to raw success count
        best = max(
            skills,
            key=lambda s: (s.success_count / max(s.success_count + s.failure_count, 1), s.success_count)
        )
        
        # Return recovery if we have steps (even if not yet verified successful)
        if best.recovery_steps:
            return "\n".join(f"{i+1}. {step}" for i, step in enumerate(best.recovery_steps))
        return None

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _is_error(self, result: str) -> bool:
        """Check if a tool result contains an error."""
        error_indicators = [
            "ERROR:", "Error:", "error:",
            "Traceback (most recent call last)",
            "Failed:", "FAILED:",
            "Exception:", "No module named",
            "SyntaxError", "IndentationError",
            "Permission denied", "FileNotFoundError",
            "timeout", "Timeout", "timed out",
            "rate limit", "RateLimitError",
            "API error", "API request failed",
            "400", "401", "403", "404", "500",  # HTTP errors
        ]
        result_lower = result.lower()
        return any(indicator.lower() in result_lower for indicator in error_indicators)

    def _extract_error_message(self, result: str) -> str:
        """Extract the core error message."""
        lines = result.strip().split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith(" "):
                # First non-indented line is usually the error
                return stripped[:200]
        return lines[0][:200] if lines else result[:200]

    def _skill_id_for_error(self, error_message: str) -> str:
        """Generate a stable skill ID from error message."""
        # Extract key words
        words = re.findall(r"[A-Za-z_]+", error_message.lower())
        key = "_".join(w for w in words[:4] if len(w) > 3)
        return f"err-{hashlib.md5(key.encode()).hexdigest()[:8]}"

    def _generate_recovery_steps(
        self, event: ErrorEvent, pattern: dict
    ) -> list[str]:
        """Generate concrete recovery steps from error context."""
        steps = []
        error_msg = event.error_message

        # Add pattern-specific steps
        if "ModuleNotFoundError" in error_msg:
            match = re.search(r"No module named ['\"]([^'\"]+)['\"]", error_msg)
            if match:
                module = match.group(1)
                steps.append(f"pip install {module}  # or pip3 install {module}")
                steps.append(f"Verify: python3 -c 'import {module.split('.')[0]}'")
                steps.append("Retry the failed operation")

        elif "SyntaxError" in error_msg or "IndentationError" in error_msg:
            steps.append("Read the file with the syntax error")
            steps.append("Fix the syntax issue (check indentation, brackets, quotes)")
            steps.append("Verify: python3 -m py_compile <file>")
            steps.append("Retry the operation")

        elif "FileNotFoundError" in error_msg:
            match = re.search(r"\[Errno 2\] No such file or directory: '([^']+)'", error_msg)
            if match:
                path = match.group(1)
                steps.append(f"Check if parent directory exists: ls -la {Path(path).parent}")
                steps.append(f"Create directory if needed: mkdir -p {Path(path).parent}")
                steps.append(f"Create file: touch {path}")
            steps.append("Retry the operation")

        elif "Permission denied" in error_msg:
            steps.append("Check file permissions: ls -la <path>")
            steps.append("Fix permissions: chmod +x <script>  or  chmod 644 <file>")
            steps.append("Retry the operation")

        elif any(t in error_msg.lower() for t in ["timeout", "timed out"]):
            steps.append("Increase timeout value if configurable")
            steps.append("Check if the operation is actually completing")
            steps.append("Consider optimizing the operation or breaking it into smaller steps")

        elif any(t in error_msg for t in ["400", "401", "403", "404", "500"]):
            steps.append("Check API endpoint URL is correct")
            steps.append("Verify API key / authentication is valid")
            steps.append("Check request format matches API requirements")
            steps.append("Retry with corrected request")

        else:
            steps.append(f"Investigate error: {error_msg[:100]}")
            steps.append("Identify root cause from error message")
            steps.append("Apply fix based on root cause")
            steps.append("Verify fix works")

        # Add tool-specific recovery
        if event.tool_name == "bash":
            # Check if the command is in PATH
            if "not found" in error_msg.lower():
                cmd = event.tool_args.get("command", "").split()[0] if event.tool_args.get("command") else ""
                if cmd:
                    steps.append(f"Check if '{cmd}' is installed: which {cmd}")
                    steps.append(f"Install if needed: brew install {cmd}  # macOS")

        return steps

    def _render_skill_md(self, skill: LearnedSkill) -> str:
        """Render a LearnedSkill as SKILL.md content."""
        now = datetime.utcnow().strftime("%Y-%m-%d")
        examples = "\n".join(f"- `{ex}`" for ex in skill.error_examples)
        steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(skill.recovery_steps))

        return f"""---
name: {skill.name}
description: {skill.description}
trigger: {skill.trigger}
root_cause: {skill.root_cause}
created: {now}
successes: {skill.success_count}
failures: {skill.failure_count}
tags: [self-evolution, error-recovery]
---

# {skill.name}

**Root Cause:** {skill.root_cause}

**Trigger:** `{skill.trigger}`

**Recovery Steps:**

{steps}

**Verification:** {skill.verification}

**Error Examples:**

{examples}

**Stats:** {skill.success_count} successes, {skill.failure_count} failures

---

*Auto-generated by Nexus Self-Evolution Engine*
"""

    def _log_error(self, event: ErrorEvent) -> None:
        """Append error event to the error log."""
        with open(self.error_log_path, "a") as f:
            f.write(json.dumps({
                "timestamp": event.timestamp,
                "error": event.error_message,
                "tool": event.tool_name,
                "tool_args": event.tool_args,
                "recovery": event.recovery_used,
                "recovery_succeeded": event.recovery_succeeded,
                "task_context": event.task_context,
            }) + "\n")

    def _save_skill_metadata(self, skill: LearnedSkill) -> None:
        """Update skill success/failure counts in the SKILL.md frontmatter."""
        skill_path = self.skills_dir / f"{skill.name}.md"
        if skill_path.exists():
            content = skill_path.read_text()
            # Update frontmatter counts
            content = re.sub(
                r"successes: \d+",
                f"successes: {skill.success_count}",
                content,
            )
            content = re.sub(
                r"failures: \d+",
                f"failures: {skill.failure_count}",
                content,
            )
            skill_path.write_text(content)

    def load_existing_skills(self) -> None:
        """Load previously learned skills from the skills directory.
        
        Parses both frontmatter (metadata) and markdown body (recovery steps).
        """
        for path in self.skills_dir.glob("*.md"):
            try:
                content = path.read_text()
                # Parse frontmatter
                match = re.match(r"^---\n(.*?)\n---\n*(.*)$", content, re.DOTALL)
                if match:
                    fm_text, body = match.group(1), match.group(2)
                    fm = {}
                    for line in fm_text.split("\n"):
                        if ": " in line:
                            k, v = line.split(": ", 1)
                            fm[k.strip()] = v.strip()
                    
                    if fm.get("tags") and "self-evolution" in fm["tags"]:
                        # Extract recovery steps from markdown body
                        recovery_steps = self._extract_recovery_steps_from_body(body)
                        
                        skill = LearnedSkill(
                            name=fm.get("name", path.stem),
                            description=fm.get("description", ""),
                            trigger=fm.get("trigger", ""),
                            root_cause=fm.get("root_cause", ""),
                            recovery_steps=recovery_steps,
                            verification=fm.get("verification", ""),
                            success_count=int(fm.get("successes", 0)),
                            failure_count=int(fm.get("failures", 0)),
                        )
                        self._skills_cache[skill.name] = skill
            except Exception:
                pass

    def _extract_recovery_steps_from_body(self, body: str) -> list[str]:
        """Extract numbered recovery steps from markdown body."""
        steps = []
        # Match lines like "1. step text" or "1. step text # comment"
        for line in body.split("\n"):
            m = re.match(r"^\s*\d+\.\s+(.+?)(?:\s+#.*)?$", line.strip())
            if m:
                step = m.group(1).strip()
                if step:
                    steps.append(step)
        return steps
