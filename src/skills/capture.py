"""
Mistake Capture System — Nexus Self-Improvement

Captures what went wrong during implementation and stores patterns
for future prevention. Part of the self-improvement loop.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from enum import Enum


class MistakeCategory(Enum):
    """Categories of mistakes the system can capture."""
    TDD_VIOLATION = "tdd_violation"           # Test not written first
    SECURITY_ISSUE = "security_issue"         # Security vulnerability found
    LOGIC_ERROR = "logic_error"               # Incorrect business logic
    EDGE_CASE = "edge_case"                   # Missing edge case handling
    SPEC_MISUNDERSTANDING = "spec_misunderstanding"  # Requirements unclear
    CONTEXT_LEAK = "context_leak"             # Context polluted/bloated
    VERIFICATION_SKIP = "verification_skip"    # Verification gate bypassed
    REGRESSION = "regression"                 # New failures introduced
    ARCHITECTURE = "architecture"              # Architectural decision issue
    OTHER = "other"


@dataclass
class MistakeRecord:
    """A single mistake captured during execution."""
    category: str
    description: str
    task_id: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    error_message: Optional[str] = None
    fix_applied: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return asdict(self)


class MistakeCapture:
    """
    Captures mistakes during execution and stores them for pattern analysis.
    
    Usage:
        capture = MistakeCapture("/path/to/project/.nexus/mistakes/")
        capture.record(
            category=MistakeCategory.LOGIC_ERROR,
            description="Off-by-one error in pagination",
            task_id="task-42",
            file_path="src/api/users.py",
            line_number=142
        )
    """
    
    def __init__(self, storage_dir: str = "~/.nexus/mistakes"):
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._current_session: Optional[str] = None
    
    def start_session(self, session_id: str) -> None:
        """Mark the start of a new capture session."""
        self._current_session = session_id
    
    def record(
        self,
        category: MistakeCategory,
        description: str,
        task_id: str,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
        error_message: Optional[str] = None,
        fix_applied: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> MistakeRecord:
        """
        Record a mistake that occurred during execution.
        
        Returns the MistakeRecord for potential immediate review.
        """
        record = MistakeRecord(
            category=category.value,
            description=description,
            task_id=task_id,
            file_path=file_path,
            line_number=line_number,
            error_message=error_message,
            fix_applied=fix_applied,
            session_id=self._current_session,
            tags=tags or [],
        )
        
        self._save_record(record)
        return record
    
    def _save_record(self, record: MistakeRecord) -> None:
        """Save a mistake record to disk."""
        filename = f"{record.category}_{int(record.timestamp * 1000)}.json"
        filepath = self.storage_dir / filename
        
        with open(filepath, 'w') as f:
            json.dump(record.to_dict(), f, indent=2)
    
    def get_recent(self, limit: int = 20) -> list[MistakeRecord]:
        """Get the most recent mistake records."""
        records = []
        for filepath in sorted(self.storage_dir.glob("*.json"), reverse=True)[:limit]:
            with open(filepath) as f:
                records.append(MistakeRecord(**json.load(f)))
        return records
    
    def get_by_category(self, category: MistakeCategory) -> list[MistakeRecord]:
        """Get all mistakes of a specific category."""
        records = []
        for filepath in self.storage_dir.glob(f"{category.value}_*.json"):
            with open(filepath) as f:
                records.append(MistakeRecord(**json.load(f)))
        return records
    
    def get_patterns(self) -> dict[str, list[str]]:
        """
        Analyze stored mistakes and extract recurring patterns.
        
        Returns a dict mapping pattern descriptions to frequency.
        """
        patterns: dict[str, int] = {}
        
        for filepath in self.storage_dir.glob("*.json"):
            with open(filepath) as f:
                data = json.load(f)
                key = f"{data['category']}:{data['description'][:50]}"
                patterns[key] = patterns.get(key, 0) + 1
        
        # Sort by frequency and return as list grouping
        sorted_patterns = sorted(patterns.items(), key=lambda x: -x[1])
        result: dict[str, list[str]] = {}
        for key, count in sorted_patterns[:10]:
            cat = key.split(":")[0]
            if cat not in result:
                result[cat] = []
            result[cat].append(f"{key} (×{count})")
        
        return result
    
    def get_actionable_insights(self) -> list[str]:
        """
        Get actionable insights from recent mistakes.
        
        These are concrete recommendations for preventing future mistakes.
        """
        insights = []
        recent = self.get_recent(limit=50)
        
        # Group by category
        by_category: dict[str, int] = {}
        for record in recent:
            by_category[record.category] = by_category.get(record.category, 0) + 1
        
        # Generate insights for common mistake types
        if by_category.get(MistakeCategory.TDD_VIOLATION.value, 0) >= 3:
            insights.append(
                "TDD violations are recurring: enforce test-first gate strictly"
            )
        
        if by_category.get(MistakeCategory.SECURITY_ISSUE.value, 0) >= 2:
            insights.append(
                "Security issues detected: run security scan on every commit"
            )
        
        if by_category.get(MistakeCategory.EDGE_CASE.value, 0) >= 3:
            insights.append(
                "Edge cases being missed: add explicit edge case analysis step"
            )
        
        if by_category.get(MistakeCategory.CONTEXT_LEAK.value, 0) >= 2:
            insights.append(
                "Context budget pressure: implement proactive compaction"
            )
        
        if by_category.get(MistakeCategory.VERIFICATION_SKIP.value, 0) >= 1:
            insights.append(
                "Verification gates being skipped: make gates mandatory"
            )
        
        return insights
