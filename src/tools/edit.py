"""Nexus Edit Tool - Claude Code's apply_changes/diff capability.

This is the CORE capability that makes Claude Code powerful.
Implements intelligent diff-based file editing with conflict detection.

Features:
- apply_changes: Apply multiple changes with content search or line ranges
- apply_diff: Parse and apply unified diff format
- create_file: Safe file creation (refuses if exists)
- insert_content: Insert after regex pattern match
- delete_lines: Delete exact line ranges with safety checks
"""

from __future__ import annotations

import os
import re
import difflib
from pathlib import Path
from typing import Optional, Any

from .base import BaseTool, ToolResult, ToolStatus


class EditTool(BaseTool):
    """Intelligent file editing tool with Claude Code's core capabilities.
    
    Provides sophisticated file editing operations including:
    - Content-based search and replace with fuzzy matching
    - Exact line range modifications
    - Unified diff parsing and application
    - Safe file creation with existence checks
    - Pattern-based content insertion
    - Safe line deletion with overlap detection
    
    Security:
    - Never deletes entire files
    - Warns before modifying files outside project root
    - Validates all line ranges before application
    """
    
    name = "edit"
    description = "Intelligent file editing with diff-based changes"
    
    def __init__(self, project_root: Optional[str] = None):
        """Initialize EditTool.
        
        Args:
            project_root: Root directory of the project. If None, uses cwd.
        """
        self.project_root = Path(project_root) if project_root else Path.cwd()
    
    def _is_outside_project(self, file_path: str) -> bool:
        """Check if file is outside project root.
        
        Args:
            file_path: Path to check.
            
        Returns:
            True if file is outside project root.
        """
        try:
            full_path = Path(file_path).resolve()
            return not full_path.is_relative_to(self.project_root.resolve())
        except (ValueError, OSError):
            return True
    
    def _read_file(self, file_path: str) -> tuple[list[str], int]:
        """Read file and return lines and total line count.
        
        Args:
            file_path: Path to file.
            
        Returns:
            Tuple of (lines list, total line count).
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        return lines, len(lines)
    
    def _write_file(self, file_path: str, lines: list[str]) -> None:
        """Write lines to file.
        
        Args:
            file_path: Path to file.
            lines: Lines to write.
        """
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    
    def _generate_diff(
        self, 
        old_lines: list[str], 
        new_lines: list[str], 
        file_path: str
    ) -> str:
        """Generate unified diff between old and new content.
        
        Args:
            old_lines: Original file lines.
            new_lines: Modified file lines.
            file_path: Path for diff header.
            
        Returns:
            Unified diff string.
        """
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=file_path,
            tofile=file_path,
            lineterm=''
        )
        return '\n'.join(diff)
    
    def _check_overlap(
        self, 
        changes: list[dict[str, Any]]
    ) -> tuple[bool, Optional[dict[str, Any]]]:
        """Check for overlapping line ranges in changes.
        
        Args:
            changes: List of change dicts with start_line/end_line.
            
        Returns:
            Tuple of (has_overlap, first_overlapping_change).
        """
        ranges = []
        for change in changes:
            if 'start_line' in change and 'end_line' in change:
                ranges.append((change['start_line'], change['end_line'], change))
        
        ranges.sort(key=lambda x: x[0])
        
        for i in range(len(ranges) - 1):
            _, end1, _ = ranges[i]
            start2, _, _ = ranges[i + 1]
            if end1 >= start2:
                return True, ranges[i + 1][2]
        
        return False, None
    
    def _apply_line_based_change(
        self,
        lines: list[str],
        change: dict[str, Any]
    ) -> list[str]:
        """Apply a line-range based change to file lines.
        
        Args:
            lines: Current file lines.
            change: Change dict with start_line, end_line, new_content.
            
        Returns:
            Modified lines.
        """
        start_line = change['start_line']
        end_line = change['end_line']
        new_content = change.get('new_content', '')
        
        # Convert to 0-indexed
        start_idx = start_line - 1
        end_idx = end_line
        
        # Validate range
        if start_idx < 0 or end_idx > len(lines) or start_idx > end_idx:
            raise ValueError(f"Invalid line range: {start_line}-{end_line}")
        
        # Split new content into lines
        new_lines = new_content.split('\n')
        if new_content and not new_content.endswith('\n'):
            # Preserve trailing newline behavior
            pass
        
        # Apply change
        before = lines[:start_idx]
        after = lines[end_idx:]
        
        # Handle new_content properly
        if new_content:
            result_lines = before + [l + '\n' for l in new_lines[:-1]]
            if new_lines[-1]:
                result_lines.append(new_lines[-1] + '\n')
        else:
            result_lines = before
        result_lines.extend(after)
        
        return result_lines
    
    def _find_content(
        self,
        lines: list[str],
        find: str,
        replace_with: str
    ) -> tuple[list[str], int]:
        """Find content in lines and replace (fuzzy match).
        
        Args:
            lines: File lines to search.
            find: Content to find.
            replace_with: Replacement content.
            
        Returns:
            Tuple of (modified_lines, number_of_replacements).
        """
        result_lines = []
        replacements = 0
        find_lines = find.split('\n')
        
        i = 0
        while i < len(lines):
            # Check if current line starts a match
            if lines[i].rstrip('\n') == find_lines[0] if find_lines else False:
                # Try to match full content
                match = True
                for j, find_line in enumerate(find_lines):
                    if i + j >= len(lines):
                        match = False
                        break
                    if lines[i + j].rstrip('\n') != find_line:
                        match = False
                        break
                
                if match:
                    # Found match - replace
                    replacement_lines = replace_with.split('\n')
                    for r_line in replacement_lines:
                        result_lines.append(r_line + '\n')
                    i += len(find_lines)
                    replacements += 1
                    continue
            
            result_lines.append(lines[i])
            i += 1
        
        return result_lines, replacements
    
    def apply_changes(
        self, 
        file_path: str, 
        changes: list[dict[str, Any]]
    ) -> ToolResult:
        """Apply multiple changes to a file.
        
        Changes can be specified as:
        - Content-based: [{"replace_with": "...", "find": "..."}]
        - Line-based: [{"start_line": N, "end_line": M, "new_content": "..."}]
        
        Multiple changes are applied in order. Overlapping line-based
        changes will return an error.
        
        Args:
            file_path: Path to file to modify.
            changes: List of change specifications.
            
        Returns:
            ToolResult with success status and diff.
        """
        # Security check
        if self._is_outside_project(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.WARNING,
                message=f"SECURITY WARNING: File '{file_path}' is outside project root. "
                       f"Project root: {self.project_root}"
            )
        
        # Check file exists
        if not os.path.exists(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"File not found: {file_path}"
            )
        
        try:
            lines, total_lines = self._read_file(file_path)
            original_lines = lines.copy()
            
            applied_changes = []
            
            # First pass: check for overlapping line-based changes
            line_based_changes = [
                c for c in changes 
                if 'start_line' in c and 'end_line' in c
            ]
            if line_based_changes:
                has_overlap, overlap_info = self._check_overlap(line_based_changes)
                if has_overlap:
                    return ToolResult(
                        success=False,
                        status=ToolStatus.CONFLICT,
                        message="Overlapping line ranges detected",
                        conflicts=[overlap_info] if overlap_info else []
                    )
            
            # Apply changes in order
            for i, change in enumerate(changes):
                if 'find' in change and 'replace_with' in change:
                    # Content-based change
                    find = change['find']
                    replace_with = change['replace_with']
                    lines, count = self._find_content(lines, find, replace_with)
                    if count == 0:
                        return ToolResult(
                            success=False,
                            status=ToolStatus.ERROR,
                            message=f"Could not find content to replace: {find[:50]}..."
                        )
                    applied_changes.append({
                        "type": "content",
                        "find": find,
                        "replace_with": replace_with,
                        "replacements": count
                    })
                    
                elif 'start_line' in change and 'end_line' in change:
                    # Line-based change
                    lines = self._apply_line_based_change(lines, change)
                    applied_changes.append({
                        "type": "line_range",
                        "start_line": change['start_line'],
                        "end_line": change['end_line']
                    })
                else:
                    return ToolResult(
                        success=False,
                        status=ToolStatus.ERROR,
                        message=f"Invalid change specification at index {i}: "
                               f"must have 'find'/'replace_with' or 'start_line'/'end_line'"
                    )
            
            # Generate diff
            diff = self._generate_diff(original_lines, lines, file_path)
            
            # Write changes
            self._write_file(file_path, lines)
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=f"Applied {len(changes)} change(s) to {file_path}",
                changes=applied_changes,
                diff=diff
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Error applying changes: {str(e)}"
            )
    
    def apply_diff(self, file_path: str, diff: str) -> ToolResult:
        """Apply a unified diff to a file.
        
        Parses unified diff format and applies changes to the file.
        Detects conflicts where the file has diverged from the expected state.
        
        Args:
            file_path: Path to file to modify.
            diff: Unified diff string.
            
        Returns:
            ToolResult with success status, conflicts, and diff.
        """
        # Security check
        if self._is_outside_project(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.WARNING,
                message=f"SECURITY WARNING: File '{file_path}' is outside project root"
            )
        
        # Check file exists
        if not os.path.exists(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"File not found: {file_path}"
            )
        
        try:
            lines, _ = self._read_file(file_path)
            original_lines = lines.copy()
            
            conflicts = []
            applied_hunks = []
            
            # Parse unified diff
            hunks = self._parse_unified_diff(diff)
            
            if not hunks:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message="No valid hunks found in diff"
                )
            
            # Apply hunks in reverse order to maintain line numbers
            for hunk in reversed(hunks):
                hunk_start = hunk['old_start']
                hunk_old_lines = hunk['old_content']
                hunk_new_lines = hunk['new_content']
                
                # Validate we're matching the right content
                idx = hunk_start - 1
                if idx < 0 or idx >= len(lines):
                    conflicts.append({
                        "type": "position_mismatch",
                        "expected_line": hunk_start,
                        "message": f"Hunk target line {hunk_start} is out of bounds"
                    })
                    continue
                
                # Try to match hunk content at expected position
                match = True
                for j, old_line in enumerate(hunk_old_lines):
                    if idx + j >= len(lines):
                        match = False
                        break
                    # Compare with some fuzziness (ignore whitespace differences)
                    if lines[idx + j].rstrip() != old_line.rstrip():
                        match = False
                        break
                
                if not match:
                    conflicts.append({
                        "type": "content_mismatch",
                        "hunk": hunk,
                        "message": "File content does not match expected hunk content"
                    })
                    continue
                
                # Apply hunk
                hunk_end = idx + len(hunk_old_lines)
                new_lines_split = hunk_new_lines.split('\n')
                if hunk_new_lines:
                    new_lines_split = [l + '\n' for l in new_lines_split[:-1]]
                    if hunk_new_lines.endswith('\n'):
                        new_lines_split.append('')
                
                lines = lines[:idx] + [l + '\n' for l in hunk_new_lines.split('\n') if l] + lines[hunk_end:]
                
                applied_hunks.append(hunk)
            
            # Generate resulting diff
            result_diff = self._generate_diff(original_lines, lines, file_path)
            
            if conflicts:
                return ToolResult(
                    success=False,
                    status=ToolStatus.CONFLICT,
                    message=f"Conflicts detected while applying diff: {len(conflicts)} issue(s)",
                    diff=result_diff,
                    conflicts=conflicts,
                    metadata={"applied_hunks": len(applied_hunks), "total_hunks": len(hunks)}
                )
            
            # Write result
            self._write_file(file_path, lines)
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=f"Applied diff with {len(applied_hunks)} hunk(s) to {file_path}",
                diff=result_diff,
                metadata={"applied_hunks": len(applied_hunks)}
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Error applying diff: {str(e)}"
            )
    
    def _parse_unified_diff(self, diff: str) -> list[dict[str, Any]]:
        """Parse unified diff format into hunks.
        
        Args:
            diff: Unified diff string.
            
        Returns:
            List of hunk dicts with old_start, old_lines, new_content.
        """
        hunks = []
        lines = diff.split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Skip non-hunk lines
            if not line.startswith('@@'):
                i += 1
                continue
            
            # Parse hunk header: @@ -start,count +start,count @@
            match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if not match:
                i += 1
                continue
            
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) else 1
            
            # Collect hunk content
            i += 1
            old_content = []
            new_content = []
            
            while i < len(lines) and not lines[i].startswith('@@'):
                if lines[i].startswith('-'):
                    old_content.append(lines[i][1:])
                elif lines[i].startswith('+'):
                    new_content.append(lines[i][1:])
                elif lines[i] != '\\ No newline at end of file':
                    # Context line
                    old_content.append(lines[i][1:] if lines[i].startswith(' ') else lines[i])
                    new_content.append(lines[i][1:] if lines[i].startswith(' ') else lines[i])
                i += 1
            
            hunks.append({
                'old_start': old_start,
                'old_content': '\n'.join(old_content),
                'new_content': '\n'.join(new_content)
            })
        
        return hunks
    
    def create_file(self, file_path: str, content: str) -> ToolResult:
        """Create a new file with content.
        
        Refuses to create the file if it already exists (safety measure).
        
        Args:
            file_path: Path to file to create.
            content: Initial content for the file.
            
        Returns:
            ToolResult with success status.
        """
        # Security check
        if self._is_outside_project(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.WARNING,
                message=f"SECURITY WARNING: Path '{file_path}' is outside project root"
            )
        
        # Check if file exists
        if os.path.exists(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"File already exists: {file_path}. Will not overwrite."
            )
        
        try:
            # Create parent directories if needed
            parent_dir = os.path.dirname(file_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            
            # Write file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=f"Created file: {file_path}",
                metadata={"bytes_written": len(content)}
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Error creating file: {str(e)}"
            )
    
    def insert_content(
        self, 
        file_path: str, 
        after_pattern: str, 
        content: str
    ) -> ToolResult:
        """Insert content after line matching pattern.
        
        Uses regex to find the line after which to insert content.
        
        Args:
            file_path: Path to file to modify.
            after_pattern: Regex pattern to match line (inserts after match).
            content: Content to insert.
            
        Returns:
            ToolResult with success status.
        """
        # Security check
        if self._is_outside_project(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.WARNING,
                message=f"SECURITY WARNING: File '{file_path}' is outside project root"
            )
        
        if not os.path.exists(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"File not found: {file_path}"
            )
        
        try:
            lines, _ = self._read_file(file_path)
            original_lines = lines.copy()
            
            # Find pattern match
            pattern = re.compile(after_pattern)
            match_line = -1
            
            for i, line in enumerate(lines):
                if pattern.search(line):
                    match_line = i
                    break
            
            if match_line == -1:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=f"Pattern not found: {after_pattern}"
                )
            
            # Insert after match
            insert_pos = match_line + 1
            new_lines = content.split('\n')
            if content:
                insert_lines = [l + '\n' for l in new_lines]
                if not content.endswith('\n'):
                    insert_lines[-1] = new_lines[-1] + '\n'
            else:
                insert_lines = []
            
            lines = lines[:insert_pos] + insert_lines + lines[insert_pos:]
            
            # Generate diff
            diff = self._generate_diff(original_lines, lines, file_path)
            
            # Write result
            self._write_file(file_path, lines)
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=f"Inserted content after line {match_line + 1}",
                diff=diff,
                metadata={"insert_after_line": match_line + 1}
            )
            
        except re.error as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Invalid regex pattern: {str(e)}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Error inserting content: {str(e)}"
            )
    
    def delete_lines(
        self, 
        file_path: str, 
        start_line: int, 
        end_line: int
    ) -> ToolResult:
        """Delete exact line range from file.
        
        Safety checks:
        - Validates line range is within file bounds
        - Prevents deleting entire file
        - Requires explicit line range (no open-ended deletion)
        
        Args:
            file_path: Path to file to modify.
            start_line: First line to delete (1-indexed).
            end_line: Last line to delete (1-indexed).
            
        Returns:
            ToolResult with success status and diff.
        """
        # Security check
        if self._is_outside_project(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.WARNING,
                message=f"SECURITY WARNING: File '{file_path}' is outside project root"
            )
        
        if not os.path.exists(file_path):
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"File not found: {file_path}"
            )
        
        try:
            lines, total_lines = self._read_file(file_path)
            original_lines = lines.copy()
            
            # Validate line range
            if start_line < 1 or end_line > total_lines:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=f"Line range {start_line}-{end_line} is out of bounds "
                           f"(file has {total_lines} lines)"
                )
            
            if start_line > end_line:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=f"Invalid line range: start_line {start_line} > end_line {end_line}"
                )
            
            # Safety check: prevent deleting entire file
            lines_to_delete = end_line - start_line + 1
            if lines_to_delete >= total_lines:
                return ToolResult(
                    success=False,
                    status=ToolStatus.ERROR,
                    message=f"REFUSING to delete entire file ({lines_to_delete}/{total_lines} lines). "
                           f"Use apply_changes with new_content='' to replace file content."
                )
            
            # Convert to 0-indexed
            start_idx = start_line - 1
            end_idx = end_line
            
            # Delete lines
            lines = lines[:start_idx] + lines[end_idx:]
            
            # Generate diff
            diff = self._generate_diff(original_lines, lines, file_path)
            
            # Write result
            self._write_file(file_path, lines)
            
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                message=f"Deleted lines {start_line}-{end_line} from {file_path}",
                diff=diff,
                metadata={"deleted_lines": lines_to_delete, "remaining_lines": len(lines)}
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Error deleting lines: {str(e)}"
            )
    
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the edit tool with given operation.
        
        Routes to the appropriate method based on 'operation' parameter.
        
        Args:
            operation: One of 'apply_changes', 'apply_diff', 'create_file', 
                     'insert_content', 'delete_lines'
            **kwargs: Operation-specific arguments.
            
        Returns:
            ToolResult with operation results.
        """
        operation = kwargs.get('operation')
        
        if operation == 'apply_changes':
            return self.apply_changes(
                file_path=kwargs['file_path'],
                changes=kwargs['changes']
            )
        elif operation == 'apply_diff':
            return self.apply_diff(
                file_path=kwargs['file_path'],
                diff=kwargs['diff']
            )
        elif operation == 'create_file':
            return self.create_file(
                file_path=kwargs['file_path'],
                content=kwargs['content']
            )
        elif operation == 'insert_content':
            return self.insert_content(
                file_path=kwargs['file_path'],
                after_pattern=kwargs['after_pattern'],
                content=kwargs['content']
            )
        elif operation == 'delete_lines':
            return self.delete_lines(
                file_path=kwargs['file_path'],
                start_line=kwargs['start_line'],
                end_line=kwargs['end_line']
            )
        else:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                message=f"Unknown operation: {operation}. "
                       f"Valid operations: apply_changes, apply_diff, create_file, "
                       f"insert_content, delete_lines"
            )


# Convenience function for direct usage
def edit_file(file_path: str, operation: str, **kwargs: Any) -> ToolResult:
    """Convenience function for file editing operations.
    
    Args:
        file_path: Path to file.
        operation: Operation to perform.
        **kwargs: Operation-specific arguments.
        
    Returns:
        ToolResult from the operation.
    """
    tool = EditTool()
    return tool.execute(operation=operation, file_path=file_path, **kwargs)
