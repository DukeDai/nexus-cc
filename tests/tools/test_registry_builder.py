"""Tests for ToolRegistry.with_defaults builder."""
from __future__ import annotations

from src.tools.registry import ToolRegistry


def test_with_defaults_registers_8_tools():
    """with_defaults should register all 8 built-in tools."""
    registry = ToolRegistry.with_defaults(workdir=".")
    assert len(registry.all_tools()) == 8


def test_with_defaults_includes_read_tool():
    """The default registry should include the Read tool."""
    registry = ToolRegistry.with_defaults(workdir=".")
    assert "Read" in registry.names()