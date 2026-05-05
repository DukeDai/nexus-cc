"""CLAUDE.md loader + project root detection.

This module provides:
- CLAUDE.md discovery (walk up from current directory)
- Project root detection (.git, package.json, pyproject.toml, etc.)
- Project-aware context building for LLM calls

Claude Code's key strength: automatic project context awareness.
We replicate and extend this with CLAUDE.md support.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Optional, TypedDict


# Project markers that indicate a repository root
ROOT_MARKERS = [
    ".git",                    # Git repository
    "package.json",             # Node.js project
    "pyproject.toml",           # Python project (PEP 621)
    "setup.py",                 # Python project (legacy)
    "Cargo.toml",               # Rust project
    "go.mod",                  # Go project
    "pom.xml",                 # Maven Java project
    "build.gradle",            # Gradle Java project
    "composer.json",           # PHP project
    "requirements.txt",        # Python dependencies
    "Pipfile",                # Python Pipfile
    "Makefile",                # Build project
    "CMakeLists.txt",          # C++ CMake project
    "CLAUDE.md",               # Claude Code project config
    ".claude",                 # Claude settings directory
]


# Files to include in project context (read automatically)
CONTEXT_FILES = {
    ".py": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
    ".js": ["package.json"],
    ".ts": ["package.json", "tsconfig.json"],
    ".rs": ["Cargo.toml"],
    ".go": ["go.mod"],
    ".java": ["pom.xml", "build.gradle"],
}


def find_project_root(start: Path | str | None = None) -> Optional[Path]:
    """Find the project root by walking up looking for root markers.
    
    Args:
        start: Starting directory. Defaults to cwd.
        
    Returns:
        Path to project root, or None if not found.
    """
    if start is None:
        start = Path.cwd()
    else:
        start = Path(start).resolve()
    
    # Limit traversal to avoid infinite loops (cap at 20 levels)
    current = start
    for _ in range(20):
        for marker in ROOT_MARKERS:
            if (current / marker).exists():
                return current
        
        parent = current.parent
        if parent == current:  # Reached filesystem root
            break
        current = parent
    
    return None


def find_claude_md(start: Path | str | None = None) -> Optional[Path]:
    """Find CLAUDE.md by walking up from start.
    
    Args:
        start: Starting directory. Defaults to cwd.
        
    Returns:
        Path to CLAUDE.md, or None if not found.
    """
    if start is None:
        start = Path.cwd()
    else:
        start = Path(start).resolve()
    
    current = start
    for _ in range(20):
        claudemd = current / "CLAUDE.md"
        if claudemd.exists() and claudemd.is_file():
            return claudemd
        
        parent = current.parent
        if parent == current:
            break
        current = parent
    
    return None


def load_claude_md(workdir: Path | str | None = None) -> Optional[str]:
    """Load CLAUDE.md content if it exists.
    
    Args:
        workdir: Working directory to search from. Defaults to cwd.
        
    Returns:
        Content of CLAUDE.md, or None if not found.
    """
    claudemd = find_claude_md(workdir)
    if claudemd is None:
        return None
    
    try:
        return claudemd.read_text()
    except (OSError, IOError):
        return None


def detect_project_language(root: Path) -> list[str]:
    """Detect programming languages used in the project.
    
    Returns:
        List of language extensions found (e.g., ['.py', '.js']).
    """
    languages = set()
    for child in root.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            if child.name in ["node_modules", "__pycache__", "target", ".git", "venv", ".venv"]:
                continue
            try:
                for item in child.iterdir():
                    if item.is_file():
                        ext = item.suffix.lower()
                        if ext in {".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java", ".c", ".cpp", ".h"}:
                            languages.add(ext)
            except PermissionError:
                continue
    return sorted(languages)


class ProjectContextDict(TypedDict):
    """Type for project context dictionary."""
    root: str
    root_found: bool
    claude_md: str | None
    claude_md_found: bool
    workdir: str
    languages: list[str]
    git_branch: str | None
    git_dirty: bool
    git_ahead: int
    git_behind: int


def get_project_context(workdir: Path | str | None = None) -> ProjectContextDict:
    """Build comprehensive project context for LLM.
    
    Returns:
        Dict with keys: root, claude_md, languages, context_files, git_branch, git_status
    """
    if workdir is None:
        workdir = Path.cwd()
    else:
        workdir = Path(workdir).resolve()
    
    root = find_project_root(workdir)
    claude_md = load_claude_md(workdir)
    
    context = {
        "root": str(root) if root else str(workdir),
        "root_found": root is not None,
        "claude_md": claude_md,
        "claude_md_found": claude_md is not None,
        "workdir": str(workdir),
        "languages": detect_project_language(root or workdir) if root else [],
        "git_branch": None,
        "git_dirty": False,
        "git_ahead": 0,
        "git_behind": 0,
    }
    
    # Git info if in a git repo
    if root and (root / ".git").exists():
        try:
            # Branch
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(root), capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                context["git_branch"] = result.stdout.strip()
            
            # Status
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(root), capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                context["git_dirty"] = len(result.stdout.strip()) > 0
            
            # Ahead/behind
            result = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
                cwd=str(root), capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) == 2:
                    context["git_behind"] = int(parts[0])
                    context["git_ahead"] = int(parts[1])
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass
    
    return context  # type: ignore[return-value]


def build_llm_system_prompt(
    workdir: Path | str | None = None,
    extra_context: str | None = None,
) -> str:
    """Build the system prompt with project context.
    
    This is the RalphLoop equivalent of Claude Code's automatic context injection.
    """
    ctx = get_project_context(workdir)
    
    prompt_parts = []
    
    # Project info
    if ctx["root_found"]:
        prompt_parts.append(f"# Project Root: {ctx['root']}")
    else:
        prompt_parts.append(f"# Working Directory: {ctx['workdir']}")
    
    # Git info
    if ctx["git_branch"]:
        dirty = " (dirty)" if ctx["git_dirty"] else ""
        ahead = f", {ctx['git_ahead']} ahead" if ctx["git_ahead"] else ""
        behind = f", {ctx['git_behind']} behind" if ctx["git_behind"] else ""
        prompt_parts.append(f"# Git: {ctx['git_branch']}{dirty}{ahead}{behind}")
    
    # Languages
    if ctx["languages"]:
        prompt_parts.append(f"# Languages: {', '.join(ctx['languages'])}")
    
    # CLAUDE.md content
    if ctx["claude_md"]:
        prompt_parts.append("\n# CLAUDE.md\n")
        prompt_parts.append(ctx["claude_md"])
    
    # Extra context
    if extra_context:
        prompt_parts.append("\n# Additional Context\n")
        prompt_parts.append(extra_context)
    
    return "\n".join(prompt_parts)


class ProjectContext:
    """High-level project context manager.
    
    Usage:
        ctx = ProjectContext("/path/to/project")
        if ctx.has_claude_md():
            print(ctx.claude_md)
        if ctx.is_git_dirty():
            print("Warning: working tree is dirty")
    """
    
    def __init__(self, workdir: Path | str | None = None):
        self._workdir = workdir
        self._ctx = get_project_context(workdir)
    
    @property
    def root(self) -> Optional[Path]:
        return Path(self._ctx["root"]) if self._ctx["root_found"] else None
    
    @property
    def claude_md(self) -> Optional[str]:
        return self._ctx["claude_md"]
    
    @property
    def has_claude_md(self) -> bool:
        return self._ctx["claude_md_found"]
    
    @property
    def git_branch(self) -> Optional[str]:
        return self._ctx["git_branch"]
    
    @property
    def is_git_dirty(self) -> bool:
        return self._ctx["git_dirty"]
    
    @property
    def languages(self) -> list[str]:
        return self._ctx["languages"]
    
    def build_system_prompt(self, extra: str | None = None) -> str:
        return build_llm_system_prompt(self._workdir, extra)
    
    def __repr__(self) -> str:
        return f"ProjectContext(root={self.root}, claude_md={self.has_claude_md}, git={self.git_branch})"
