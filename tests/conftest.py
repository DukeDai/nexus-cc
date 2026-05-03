"""Pytest fixtures and configuration for Nexus tests."""

import sys
from pathlib import Path

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

import pytest


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory for file operations."""
    return tmp_path


@pytest.fixture
def sample_task_queue():
    """Sample task queue for testing."""
    return [
        {"description": "Task 1", "priority": "high"},
        {"description": "Task 2", "priority": "medium"},
        {"description": "Task 3", "priority": "low"},
    ]


@pytest.fixture
def mock_context_monitor():
    """Mock context monitor returning fixed value."""
    def _monitor(value: float = 25.0):
        return value
    return _monitor
