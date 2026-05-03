"""Write tool for creating and modifying files."""

import os
import tempfile
import shutil
from typing import Optional

from .base import BaseTool, ToolResult


class WriteTool(BaseTool):
    """Tool for writing files safely with atomic operations."""

    name = "write"
    description = "Write content to file"

    def execute(
        self,
        file_path: str,
        content: str,
        create_parents: bool = True
    ) -> ToolResult:
        """
        Write content to a file with atomic operation support.

        Args:
            file_path: Path to the file to write
            content: Content to write
            create_parents: Whether to create parent directories (default: True)

        Returns:
            ToolResult with success status or error message
        """
        # Validate inputs
        if not file_path:
            return ToolResult(success=False, error="File path is required")

        # Check for overwrite if file exists
        file_exists = os.path.isfile(file_path)
        warning = None
        if file_exists:
            warning = f"Overwriting existing file: {file_path}"

        # Create parent directories if requested
        if create_parents:
            parent_dir = os.path.dirname(file_path)
            if parent_dir:
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception as e:
                    return ToolResult(success=False, error=f"Failed to create parent directory: {str(e)}")

        try:
            # Atomic write: write to temp file, then rename
            dir_path = os.path.dirname(file_path) or "."
            fd, temp_path = tempfile.mkstemp(dir=dir_path, prefix=".write_tmp_")
            try:
                # Write content to temp file
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)

                # Set permissions to match what would be created normally
                # or inherit from parent if exists
                if file_exists:
                    stat_info = os.stat(file_path)
                    os.chmod(temp_path, stat_info.st_mode)

                # Atomic rename
                shutil.move(temp_path, file_path)

            except Exception:
                # Clean up temp file on failure
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

            return ToolResult(
                success=True,
                data={
                    "file_path": file_path,
                    "bytes_written": len(content.encode("utf-8")),
                },
                error=warning
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write file: {str(e)}")
