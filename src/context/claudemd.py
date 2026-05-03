"""CLAUDE.md Hierarchy Management.

Loads and merges CLAUDE.md files from multiple sources with priority:
1. .claude/projects/*/CLAUDE.md (project-specific, highest priority)
2. CLAUDE.local.md (local overrides)
3. CLAUDE.md (root, lowest priority)

Merging strategy:
- Sections are combined; later sources override earlier ones
- Top-level keys are overridden (not deep merged)
- Comments (#) and frontmatter (---) are preserved
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional, Any


class ClaudeMDPriority(Enum):
    """Priority order for CLAUDE.md sources (lowest to highest)."""
    ROOT = 1      # CLAUDE.md in project root
    LOCAL = 2     # CLAUDE.local.md for local overrides
    PROJECT = 3   # .claude/projects/*/CLAUDE.md (highest)


@dataclass
class ClaudeMDSection:
    """A parsed section from a CLAUDE.md file."""
    title: str
    content: str
    line_number: int
    priority: ClaudeMDPriority = ClaudeMDPriority.ROOT

    def __str__(self) -> str:
        return f"# {self.title}\n{self.content}"


@dataclass
class ClaudeMDDocument:
    """A complete CLAUDE.md document with metadata."""
    raw_content: str
    source_path: Optional[Path]
    priority: ClaudeMDPriority
    sections: list[ClaudeMDSection] = field(default_factory=list)
    frontmatter: dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.raw_content.strip()


class ClaudeMDLoader:
    """Loads CLAUDE.md files from the filesystem with priority chain.

    Priority (lowest to highest):
        1. CLAUDE.md (root)
        2. CLAUDE.local.md (local overrides)
        3. .claude/projects/*/CLAUDE.md (project-specific)

    The loader searches for project-specific CLAUDE.md files by looking
    in .claude/projects/ directory for subdirectories containing CLAUDE.md.
    """

    # Standard filenames in priority order (lowest to highest)
    STANDARD_FILES: list[tuple[str, ClaudeMDPriority]] = [
        ("CLAUDE.md", ClaudeMDPriority.ROOT),
        ("CLAUDE.local.md", ClaudeMDPriority.LOCAL),
    ]

    PROJECT_DIR_PATTERN = ".claude/projects"
    PROJECT_CLAUDE_FILENAME = "CLAUDE.md"

    def __init__(self, root_path: Optional[Path] = None):
        """Initialize loader.

        Args:
            root_path: Root directory to search from. Defaults to cwd.
        """
        self.root_path = Path(root_path) if root_path else Path.cwd()

    def find_project_claude_files(self) -> list[Path]:
        """Find all project-specific CLAUDE.md files.

        Searches in .claude/projects/*/CLAUDE.md for each project subdirectory.

        Returns:
            List of paths to project CLAUDE.md files, sorted by priority.
        """
        project_dir = self.root_path / self.PROJECT_DIR_PATTERN
        if not project_dir.exists():
            return []

        project_files = []
        if project_dir.is_dir():
            for entry in project_dir.iterdir():
                if entry.is_dir():
                    claude_path = entry / self.PROJECT_CLAUDE_FILENAME
                    if claude_path.exists():
                        project_files.append(claude_path)

        # Sort by directory name for consistent ordering
        project_files.sort(key=lambda p: p.parent.name)
        return project_files

    def load_file(self, path: Path) -> Optional[ClaudeMDDocument]:
        """Load a single CLAUDE.md file.

        Args:
            path: Path to the CLAUDE.md file.

        Returns:
            ClaudeMDDocument if file exists and is readable, None otherwise.
        """
        if not path.exists():
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return None

        priority = ClaudeMDPriority.ROOT
        if path.name == "CLAUDE.local.md":
            priority = ClaudeMDPriority.LOCAL
        elif self.PROJECT_DIR_PATTERN in str(path):
            priority = ClaudeMDPriority.PROJECT

        sections = self._parse_sections(content)
        frontmatter = self._parse_frontmatter(content)

        return ClaudeMDDocument(
            raw_content=content,
            source_path=path,
            priority=priority,
            sections=sections,
            frontmatter=frontmatter,
        )

    def _parse_frontmatter(self, content: str) -> dict[str, str]:
        """Extract YAML frontmatter from content.

        Args:
            content: Raw file content.

        Returns:
            Dict of frontmatter key-value pairs.
        """
        frontmatter = {}
        if content.startswith("---"):
            match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            if match:
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, _, value = line.partition(":")
                        frontmatter[key.strip()] = value.strip()
        return frontmatter

    def _parse_sections(self, content: str) -> list[ClaudeMDSection]:
        """Parse content into sections based on # headings.

        Args:
            content: Raw file content.

        Returns:
            List of ClaudeMDSection objects.
        """
        sections = []
        lines = content.split("\n")
        current_title = ""
        current_content: list[str] = []
        current_line = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("##"):
                if current_title:
                    sections.append(ClaudeMDSection(
                        title=current_title,
                        content="\n".join(current_content).strip(),
                        line_number=current_line,
                    ))
                current_title = stripped[2:].strip()
                current_content = []
                current_line = i + 1
            else:
                current_content.append(line)

        if current_title:
            sections.append(ClaudeMDSection(
                title=current_title,
                content="\n".join(current_content).strip(),
                line_number=current_line,
            ))

        return sections

    def load_all(self) -> list[ClaudeMDDocument]:
        """Load all CLAUDE.md files in priority order.

        Returns:
            List of ClaudeMDDocument objects sorted by priority (lowest first).
        """
        documents: list[ClaudeMDDocument] = []

        # Load standard files
        for filename, priority in self.STANDARD_FILES:
            path = self.root_path / filename
            doc = self.load_file(path)
            if doc:
                documents.append(doc)

        # Load project-specific files
        for path in self.find_project_claude_files():
            doc = self.load_file(path)
            if doc:
                documents.append(doc)

        # Sort by priority
        documents.sort(key=lambda d: d.priority.value)
        return documents


class ClaudeMD:
    """CLAUDE.md hierarchy manager with loading and merging.

    Provides a unified interface for accessing CLAUDE.md content
    with proper priority handling and section merging.
    """

    def __init__(self, root_path: Optional[Path] = None):
        """Initialize CLAUDE.md manager.

        Args:
            root_path: Root directory to search from. Defaults to cwd.
        """
        self.root_path = Path(root_path) if root_path else Path.cwd()
        self.loader = ClaudeMDLoader(self.root_path)
        self._documents: list[ClaudeMDDocument] = []
        self._merged_sections: dict[str, str] = {}
        self._merged_content: str = ""

    def load(self) -> bool:
        """Load all CLAUDE.md files and merge them.

        Returns:
            True if at least one document was loaded.
        """
        self._documents = self.loader.load_all()
        if not self._documents:
            return False

        self._merge_content()
        return True

    def _merge_content(self) -> None:
        """Merge all documents into unified content.

        Later documents override earlier ones for top-level keys.
        Section content is concatenated with separator comments.
        """
        self._merged_sections = {}

        for doc in self._documents:
            if doc.is_empty:
                continue

            for section in doc.sections:
                if section.title in self._merged_sections:
                    # Append with separator
                    self._merged_sections[section.title] += (
                        f"\n\n<!-- From {doc.source_path.name} -->\n{section.content}"
                    )
                else:
                    self._merged_sections[section.title] = section.content

        # Build merged content
        lines = ["# Merged CLAUDE.md"]
        for title, content in self._merged_sections.items():
            lines.append(f"\n## {title}\n{content}")

        self._merged_content = "\n".join(lines)

    @property
    def documents(self) -> list[ClaudeMDDocument]:
        """All loaded documents in priority order."""
        return self._documents

    @property
    def merged_content(self) -> str:
        """Combined content from all documents."""
        return self._merged_content

    @property
    def sections(self) -> dict[str, str]:
        """Merged sections keyed by title."""
        return self._merged_sections

    def get_section(self, title: str) -> Optional[str]:
        """Get a specific section's content.

        Args:
            title: Section title (without # prefix).

        Returns:
            Section content or None if not found.
        """
        return self._merged_sections.get(title)

    def get_frontmatter_value(self, key: str) -> Optional[str]:
        """Get a frontmatter value from any document.

        Checks documents in priority order (highest first).

        Args:
            key: Frontmatter key to look up.

        Returns:
            Value or None if not found.
        """
        # Check from highest priority to lowest
        for doc in reversed(self._documents):
            if key in doc.frontmatter:
                return doc.frontmatter[key]
        return None

    def has_local_override(self) -> bool:
        """Check if a CLAUDE.local.md exists."""
        return any(
            d.source_path and d.source_path.name == "CLAUDE.local.md"
            for d in self._documents
        )

    def get_project_names(self) -> list[str]:
        """Get names of projects with CLAUDE.md files."""
        return [
            d.source_path.parent.name
            for d in self._documents
            if d.priority == ClaudeMDPriority.PROJECT and d.source_path
        ]

    def __repr__(self) -> str:
        doc_count = len(self._documents)
        section_count = len(self._merged_sections)
        return (
            f"ClaudeMD(documents={doc_count}, sections={section_count}, "
            f"root={self.root_path})"
        )


# Convenience function
def load_claude_md(root_path: Optional[Path] = None) -> ClaudeMD:
    """Load and merge CLAUDE.md hierarchy.

    Args:
        root_path: Root directory to search from. Defaults to cwd.

    Returns:
        ClaudeMD instance with loaded and merged content.
    """
    claude_md = ClaudeMD(root_path)
    claude_md.load()
    return claude_md
