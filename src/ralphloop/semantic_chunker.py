"""Semantic Chunker — Intent-based Context Compression.

This module replaces threshold-based compression with semantically-aware chunking:
- Chunk by intent/decision units, not token count
- Preserve: key decisions, error lessons, dependencies
- Discard: mechanical repetition, redundant debug output
- Build summaries that maintain decision context

Key insight: Compression should preserve WHY decisions were made, not just WHAT was done.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional
import re


class ChunkType(Enum):
    """Types of semantic chunks."""
    DECISION = auto()       # A choice between alternatives
    ERROR_LESSON = auto()   # Something learned from failure
    DEPENDENCY = auto()     # Causal dependency established
    IMPLEMENTATION = auto() # Concrete implementation step
    VERIFICATION = auto()   # A test or check
    REFLECTION = auto()     # Self-analysis or learning
    SUMMARIZABLE = auto()   # Redundant/mechanical, can be summarized
    ESSENTIAL = auto()      # Critical context that must be preserved


@dataclass
class SemanticChunk:
    """A semantically meaningful chunk of conversation.

    Attributes:
        chunk_id: Unique identifier
        chunk_type: What kind of content this is
        content: The actual text content
        importance: 0.0-1.0, how important to preserve
        preservable: Whether this should survive compression
        decision_context: If DECISION, what alternatives were considered
        error_patterns: If ERROR_LESSON, what error patterns were identified
    """
    chunk_id: str
    chunk_type: ChunkType
    content: str
    importance: float = 0.5
    preservable: bool = True
    decision_context: dict = field(default_factory=dict)
    error_patterns: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CompressionDecision:
    """Result of semantic compression analysis."""
    keep: list[SemanticChunk]  # Chunks to preserve fully
    summarize: list[SemanticChunk]  # Chunks to summarize
    discard: list[SemanticChunk]  # Chunks to discard entirely
    summary: str  # Generated summary for LLM
    compression_ratio: float  # How much was compressed


# ─── Semantic Pattern Detectors ─────────────────────────────────────────────


class PatternDetector:
    """Detect semantic patterns in messages."""

    # Patterns that indicate specific chunk types
    DECISION_PATTERNS = [
        r"(?i)(chose|selected|picked|decided|going with|using)\s+(alternative|approach|method|方案|选择)",
        r"(?i)(instead|alternative|rather than|而不是)",
        r"(?i)(decided to|choice made|decision:)",
        r"(?i)(let's go with|let's use|adopting)",
    ]

    ERROR_LESSON_PATTERNS = [
        r"(?i)(learned that|lesson:|error.*(taught|showed|revealed))",
        r"(?i)(mistake|screw up|bug.*(cause|root|reason))",
        r"(?i)(the problem was|root cause|turned out to be)",
        r"(?i)(should have|would have been.*if|if only)",
    ]

    DEPENDENCY_PATTERNS = [
        r"(?i)(depends on|dependent on|prerequisite|先决条件)",
        r"(?i)(need to|first|before|after)",
        r"(?i)(required that|must.*first|has to)",
    ]

    IMPLEMENTATION_PATTERNS = [
        r"(?i)(implementing|writing|creating|coding|添加|实现)",
        r"(?i)(adding|modified|changed|updated|更新)",
        r"(?i)(created file|generated|saved|保存)",
    ]

    VERIFICATION_PATTERNS = [
        r"(?i)(test|verify|check|assert|验证)",
        r"(?i)(passes?|fails?|works?|有效)",
        r"(?i)(confirmed|validated|checked|确认)",
    ]

    SUMMARIZABLE_PATTERNS = [
        r"(?i)(retrying|trying again|重新|重试)",
        r"(?i)(debug output|logging|console\.log|print\()",
        r"(?i)(step \d+|doing|executing|执行)",
        r"(?i)(waiting for|polling|checking again)",
        # Mechanical repetition
        r"(?i)(same as above|ditto|同上|同样)",
        r"(?i)(as mentioned|as stated|如前所述)",
    ]

    # High importance keywords
    ESSENTIAL_PATTERNS = [
        r"(?i)(critical|essential|must|必须|关键)",
        r"(?i)(security|permission|auth|权限)",
        r"(?i)(breaking|irreversible|不可逆)",
        r"(?i)(deadline|time-sensitive|紧急)",
        r"(?i)(customer|production|prod|生产)",
    ]

    def detect_chunk_type(self, content: str) -> ChunkType:
        """Detect what type of semantic chunk this is."""
        content_lower = content.lower()

        # Check in order of specificity
        if self._matches_any(content_lower, self.DECISION_PATTERNS):
            return ChunkType.DECISION
        if self._matches_any(content_lower, self.ERROR_LESSON_PATTERNS):
            return ChunkType.ERROR_LESSON
        if self._matches_any(content_lower, self.DEPENDENCY_PATTERNS):
            return ChunkType.DEPENDENCY
        if self._matches_any(content_lower, self.VERIFICATION_PATTERNS):
            return ChunkType.VERIFICATION
        if self._matches_any(content_lower, self.IMPLEMENTATION_PATTERNS):
            return ChunkType.IMPLEMENTATION
        if self._matches_any(content_lower, self.SUMMARIZABLE_PATTERNS):
            return ChunkType.SUMMARIZABLE
        if self._matches_any(content_lower, self.ESSENTIAL_PATTERNS):
            return ChunkType.ESSENTIAL

        return ChunkType.IMPLEMENTATION  # Default

    def assess_importance(self, content: str, chunk_type: ChunkType) -> float:
        """Assess how important this chunk is (0.0-1.0)."""
        importance = 0.5  # Base importance

        # Chunk type affects importance
        type_importance = {
            ChunkType.DECISION: 0.9,
            ChunkType.ERROR_LESSON: 0.85,
            ChunkType.DEPENDENCY: 0.8,
            ChunkType.ESSENTIAL: 0.95,
            ChunkType.VERIFICATION: 0.6,
            ChunkType.IMPLEMENTATION: 0.5,
            ChunkType.REFLECTION: 0.6,
            ChunkType.SUMMARIZABLE: 0.2,
        }
        importance = type_importance.get(chunk_type, 0.5)

        # Essential patterns boost importance
        if self._matches_any(content.lower(), self.ESSENTIAL_PATTERNS):
            importance = min(1.0, importance + 0.1)

        # Error-related content
        if "error" in content.lower() or "fail" in content.lower():
            importance = min(1.0, importance + 0.1)

        return importance

    def should_preserve(self, content: str, chunk_type: ChunkType, importance: float) -> bool:
        """Determine if this chunk should survive compression."""
        # Never discard essential or error lessons
        if chunk_type in {ChunkType.ESSENTIAL, ChunkType.DECISION, ChunkType.ERROR_LESSON}:
            return True

        # Discard summarizable low-importance chunks
        if chunk_type == ChunkType.SUMMARIZABLE and importance < 0.4:
            return False

        # Keep higher importance chunks
        return importance >= 0.5

    def extract_decision_context(self, content: str) -> dict:
        """Extract decision alternatives from content."""
        context = {"alternatives_considered": [], "chosen": "", "reason": ""}

        # Extract alternatives
        alternatives = re.findall(r"(?:instead|rather than|而不是)([^\.,]+)", content)
        if alternatives:
            context["alternatives_considered"] = [a.strip() for a in alternatives]

        # Extract what was chosen
        chosen = re.findall(r"(?:chose|selected|picked|decided|using|采用)([^\.,]+)", content)
        if chosen:
            context["chosen"] = chosen[0].strip()

        return context

    def extract_error_patterns(self, content: str) -> list[str]:
        """Extract error patterns from content."""
        patterns = []

        # Find error mentions
        error_mentions = re.findall(r"(?:error|exception|failed|failure)(?::\s*)?([^\.,]+)", content.lower())
        patterns.extend(e.strip()[:50] for e in error_mentions[:3])

        # Find root cause mentions
        root_causes = re.findall(r"(?:root cause|turned out|was actually)(?::\s*)?([^\.,]+)", content.lower())
        patterns.extend(r.strip()[:50] for r in root_causes[:2])

        return list(set(patterns))

    def _matches_any(self, text: str, patterns: list[str]) -> bool:
        """Check if any pattern matches the text."""
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False


# ─── Semantic Chunker ─────────────────────────────────────────────────────────


class SemanticChunker:
    """Semantically-aware message chunker and compressor.

    Instead of compressing by token count, this chunker:
    1. Identifies semantic chunks (decisions, errors, dependencies)
    2. Preserves high-importance chunks (decisions, error lessons)
    3. Summarizes or discards low-importance chunks (mechanical repetition)
    4. Builds coherent summary from remaining context

    Usage:
        chunker = SemanticChunker()
        chunks = chunker.chunk_messages(messages)
        result = chunker.compress(chunks, budget_percent=50)
        # result.summary can be injected into LLM
    """

    # Importance threshold for preservation
    PRESERVE_THRESHOLD = 0.5

    # How many chunks to preserve at each importance level
    MAX_PRESERVE_DECISIONS = 10
    MAX_PRESERVE_ERROR_LESSONS = 10
    MAX_PRESERVE_DEPENDENCIES = 5
    MAX_PRESERVE_IMPLEMENTATION = 20

    def __init__(self):
        self._detector = PatternDetector()
        self._chunk_counter = 0

    def chunk_messages(self, messages: list[dict]) -> list[SemanticChunk]:
        """Convert messages into semantic chunks.

        Args:
            messages: List of message dicts with 'role' and 'content'

        Returns:
            List of SemanticChunks with typed, importance-scored content
        """
        chunks = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if not content or len(content.strip()) < 10:
                continue

            # Detect chunk type
            chunk_type = self._detector.detect_chunk_type(content)

            # Assess importance
            importance = self._detector.assess_importance(content, chunk_type)

            # Determine if preservable
            preservable = self._detector.should_preserve(content, chunk_type, importance)

            # Extract context for special chunk types
            decision_context = {}
            error_patterns = []
            if chunk_type == ChunkType.DECISION:
                decision_context = self._detector.extract_decision_context(content)
            elif chunk_type == ChunkType.ERROR_LESSON:
                error_patterns = self._detector.extract_error_patterns(content)

            self._chunk_counter += 1
            chunk = SemanticChunk(
                chunk_id=f"chunk_{self._chunk_counter}_{chunk_type.name}",
                chunk_type=chunk_type,
                content=content,
                importance=importance,
                preservable=preservable,
                decision_context=decision_context,
                error_patterns=error_patterns,
            )
            chunks.append(chunk)

        return chunks

    def compress(
        self,
        chunks: list[SemanticChunk],
        budget_percent: float = 50.0,
        target_ratio: float = 0.5
    ) -> CompressionDecision:
        """Compress chunks based on semantic importance.

        Args:
            chunks: List of SemanticChunks to compress
            budget_percent: Current budget pressure (0-100)
            target_ratio: Target compression ratio (0.0-1.0)

        Returns:
            CompressionDecision with keep/summarize/discard lists and summary
        """
        if not chunks:
            return CompressionDecision(
                keep=[], summarize=[], discard=[], summary="", compression_ratio=1.0
            )

        # Categorize chunks
        keep = []
        summarize = []
        discard = []

        for chunk in chunks:
            if not chunk.preservable:
                discard.append(chunk)
            elif chunk.importance >= self.PRESERVE_THRESHOLD:
                keep.append(chunk)
            else:
                summarize.append(chunk)

        # Sort keep by importance descending
        keep.sort(key=lambda c: c.importance, reverse=True)

        # Cap preserved chunks by type
        keep = self._cap_preserved(keep)

        # Generate summary from summarized chunks
        summary = self._generate_summary(summarize, keep)

        # Calculate compression ratio
        original = len(chunks)
        kept = len(keep)
        compression_ratio = kept / original if original > 0 else 1.0

        return CompressionDecision(
            keep=keep,
            summarize=summarize,
            discard=discard,
            summary=summary,
            compression_ratio=compression_ratio,
        )

    def compress_to_budget(
        self,
        chunks: list[SemanticChunk],
        max_chunks: int
    ) -> CompressionDecision:
        """Compress chunks to fit within a maximum count.

        Args:
            chunks: List of SemanticChunks to compress
            max_chunks: Maximum number of chunks to keep

        Returns:
            CompressionDecision with reduced chunk set
        """
        if len(chunks) <= max_chunks:
            return CompressionDecision(
                keep=chunks,
                summarize=[],
                discard=[],
                summary=self._generate_summary([], chunks),
                compression_ratio=1.0,
            )

        # Sort by importance and take top N
        sorted_chunks = sorted(chunks, key=lambda c: c.importance, reverse=True)
        kept = sorted_chunks[:max_chunks]
        to_summarize = sorted_chunks[max_chunks:]

        summary = self._generate_summary(to_summarize, kept)

        return CompressionDecision(
            keep=kept,
            summarize=[],
            discard=[],
            summary=summary,
            compression_ratio=max_chunks / len(chunks),
        )

    def _cap_preserved(self, chunks: list[SemanticChunk]) -> list[SemanticChunk]:
        """Cap the number of preserved chunks by type."""
        result = []
        counts = {
            ChunkType.DECISION: 0,
            ChunkType.ERROR_LESSON: 0,
            ChunkType.DEPENDENCY: 0,
            ChunkType.IMPLEMENTATION: 0,
            ChunkType.ESSENTIAL: 999,  # No cap
        }
        max_map = {
            ChunkType.DECISION: self.MAX_PRESERVE_DECISIONS,
            ChunkType.ERROR_LESSON: self.MAX_PRESERVE_ERROR_LESSONS,
            ChunkType.DEPENDENCY: self.MAX_PRESERVE_DEPENDENCIES,
            ChunkType.IMPLEMENTATION: self.MAX_PRESERVE_IMPLEMENTATION,
            ChunkType.ESSENTIAL: 999,
            ChunkType.VERIFICATION: 10,
            ChunkType.REFLECTION: 5,
        }

        for chunk in chunks:
            max_count = max_map.get(chunk.chunk_type, 10)
            if counts.get(chunk.chunk_type, 0) < max_count:
                result.append(chunk)
                counts[chunk.chunk_type] = counts.get(chunk.chunk_type, 0) + 1

        return result

    def _generate_summary(
        self,
        summarized: list[SemanticChunk],
        preserved: list[SemanticChunk]
    ) -> str:
        """Generate a coherent summary from chunks.

        Builds summary that preserves decision context and error lessons.
        """
        lines = ["[Compressed Context Summary]"]

        # Summarize decisions
        decisions = [c for c in preserved if c.chunk_type == ChunkType.DECISION]
        if decisions:
            lines.append("\n## Key Decisions")
            for d in decisions[:5]:
                ctx = d.decision_context
                if ctx.get("chosen"):
                    lines.append(f"- Chose: {ctx['chosen'][:80]}")
                if ctx.get("alternatives_considered"):
                    alts = ", ".join(a[:30] for a in ctx["alternatives_considered"][:2])
                    lines.append(f"  (vs {alts})")

        # Summarize error lessons
        errors = [c for c in preserved if c.chunk_type == ChunkType.ERROR_LESSON]
        if errors:
            lines.append("\n## Error Lessons")
            for e in errors[:5]:
                if e.error_patterns:
                    lines.append(f"- {e.error_patterns[0][:80]}")
                else:
                    lines.append(f"- {e.content[:80]}")

        # Summarize dependencies
        deps = [c for c in preserved if c.chunk_type == ChunkType.DEPENDENCY]
        if deps:
            lines.append("\n## Dependencies")
            for d in deps[:5]:
                lines.append(f"- {d.content[:80]}")

        # Count summarized items
        if summarized:
            lines.append(f"\n[Note: {len(summarized)} routine items compressed]")

        return "\n".join(lines)

    def build_injection_summary(
        self,
        chunks: list[SemanticChunk],
        phase: str
    ) -> str:
        """Build a phase-specific injection summary.

        Different phases need different information:
        - PLAN: decisions, dependencies
        - ACT: implementation progress
        - VERIFY: verification results
        - REFLECT: error lessons
        """
        chunks.sort(key=lambda c: c.importance, reverse=True)

        if phase == "PLAN":
            relevant = [c for c in chunks if c.chunk_type in {
                ChunkType.DECISION, ChunkType.DEPENDENCY, ChunkType.ESSENTIAL
            }]
        elif phase == "ACT":
            relevant = [c for c in chunks if c.chunk_type in {
                ChunkType.IMPLEMENTATION, ChunkType.ESSENTIAL
            }]
        elif phase == "VERIFY":
            relevant = [c for c in chunks if c.chunk_type in {
                ChunkType.VERIFICATION, ChunkType.ERROR_LESSON, ChunkType.ESSENTIAL
            }]
        elif phase == "REFLECT":
            relevant = [c for c in chunks if c.chunk_type in {
                ChunkType.ERROR_LESSON, ChunkType.REFLECTION, ChunkType.DECISION
            }]
        else:
            relevant = chunks[:10]

        lines = [f"[{phase} Phase Context Summary]"]

        for chunk in relevant[:15]:
            prefix = {
                ChunkType.DECISION: "DECISION:",
                ChunkType.ERROR_LESSON: "LESSON:",
                ChunkType.DEPENDENCY: "DEP:",
                ChunkType.IMPLEMENTATION: "IMPL:",
                ChunkType.VERIFICATION: "VERIFY:",
                ChunkType.ESSENTIAL: "CRITICAL:",
            }.get(chunk.chunk_type, "")

            content = chunk.content[:100]
            lines.append(f"- [{prefix}] {content}")

        return "\n".join(lines)