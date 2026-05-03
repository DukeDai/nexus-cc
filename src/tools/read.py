"""Read tool for file content reading."""

import os
import warnings
from typing import Optional

from .base import BaseTool, ToolResult


# Size thresholds
LARGE_FILE_WARN = 100 * 1024   # 100KB - warn user
LARGE_FILE_TRUNCATE = 500 * 1024  # 500KB - truncate content


def _detect_binary(file_path: str) -> bool:
    """Check if a file appears to be binary."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            # Check for null bytes or high ratio of non-text bytes
            if b"\x00" in chunk:
                return True
            # Simple heuristic: if more than 30% non-printable, consider binary
            text_chars = bytes(range(32, 127)) + b"\n\r\t"
            non_text = sum(1 for b in chunk if b not in text_chars)
            if len(chunk) > 0 and non_text / len(chunk) > 0.30:
                return True
    except Exception:
        return True  # Treat read errors as binary
    return False


class ReadTool(BaseTool):
    """Tool for reading file contents safely."""

    name = "read"
    description = "Read file contents"

    def execute(self, file_path: str, offset: int = 1, limit: int = 500) -> ToolResult:
        """
        Read file contents with line number support and safety checks.

        Args:
            file_path: Path to the file to read
            offset: Starting line number (1-indexed, default: 1)
            limit: Maximum number of lines to read (default: 500)

        Returns:
            ToolResult with file contents or error message
        """
        # Validate inputs
        if offset < 1:
            return ToolResult(success=False, error="Offset must be >= 1")

        if limit < 1:
            return ToolResult(success=False, error="Limit must be >= 1")

        # Check file exists
        if not os.path.isfile(file_path):
            return ToolResult(success=False, error=f"File not found: {file_path}")

        # Check for binary file
        if _detect_binary(file_path):
            return ToolResult(success=False, error="Cannot read binary file")

        # Check file size
        file_size = os.path.getsize(file_path)

        warning = None
        if file_size > LARGE_FILE_TRUNCATE:
            warning = f"File is very large ({file_size} bytes), content will be truncated"
            limit = min(limit, 100)  # Drastically limit for huge files
        elif file_size > LARGE_FILE_WARN:
            warning = f"File is large ({file_size} bytes), reading may be slow"

        # Try UTF-8 first, fallback to latin-1
        encoding = "utf-8"
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            encoding = "latin-1"
            try:
                with open(file_path, "r", encoding="latin-1") as f:
                    content = f.read()
            except Exception as e:
                return ToolResult(success=False, error=f"Failed to read file: {str(e)}")

        # Split into lines
        lines = content.splitlines()

        # Apply offset and limit (convert to 0-indexed)
        start_idx = max(0, offset - 1)
        end_idx = min(len(lines), start_idx + limit)
        selected_lines = lines[start_idx:end_idx]

        # Format with line numbers
        result_lines = []
        for i, line in enumerate(selected_lines, start=offset):
            result_lines.append(f"{i}|{line}")

        result_content = "\n".join(result_lines)

        return ToolResult(
            success=True,
            data={
                "content": result_content,
                "file_path": file_path,
                "encoding": encoding,
                "total_lines": len(lines),
                "returned_lines": len(selected_lines),
            },
            error=warning
        )
