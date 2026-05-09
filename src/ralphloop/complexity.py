"""Task complexity classification for P6 auto-decompose."""

from __future__ import annotations
from enum import Enum, auto


class TaskComplexity(Enum):
    """Task complexity levels for auto-decomposition."""
    SIMPLE = auto()      # Single step, one file, no decomposition needed
    MODERATE = auto()    # 2-3 steps, still manageable
    COMPLEX = auto()     # Multi-step, cross-module, needs decomposition


# Complexity signal keywords
_COMPLEX_KEYWORDS = {
    "refactor", "redesign", "migrate", "benchmark", "audit",
    "implement feature", "build system", "multi-module",
    "performance optimization", "security review", "integration",
}
_MODERATE_KEYWORDS = {
    "add endpoint", "fix bug", "update config", "write test",
    "create module", "add field", "implement handler",
    "multiple files", "several", "various",
}


def _count_structural_steps(task: str) -> int:
    """Estimate step count from structural patterns."""
    text = task.lower()
    count = 1
    # Split on conjunctions that suggest multiple steps
    separators = [' and then ', ' after that ', '; then ', '\n- ', '\n  - ']
    for sep in separators:
        count += text.count(sep)
    return max(count, 1)


def classify_complexity(task: str) -> TaskComplexity:
    """Classify task complexity using heuristic signals.

    Uses keyword matching + structural pattern counting to avoid
    expensive LLM calls at entry point.
    """
    task_lower = task.lower()
    step_count = _count_structural_steps(task_lower)

    # Check for explicit complexity keywords
    complex_kw_count = sum(1 for kw in _COMPLEX_KEYWORDS if kw in task_lower)
    moderate_kw_count = sum(1 for kw in _MODERATE_KEYWORDS if kw in task_lower)

    # Multi-step signal
    if step_count >= 4 or complex_kw_count >= 2:
        return TaskComplexity.COMPLEX
    if step_count >= 2 or moderate_kw_count >= 1:
        return TaskComplexity.MODERATE
    return TaskComplexity.SIMPLE
