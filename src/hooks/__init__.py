"""Hooks system for Nexus — PreToolUse, PostToolUse, PreAgent, PostAgent, etc."""

from .hook_manager import HookManager, HookEvent, HookHandler
from .pre_tool_hook import PreToolHook, PreToolContext
from .post_tool_hook import PostToolHook, PostToolContext

__all__ = [
    "HookManager",
    "HookEvent",
    "HookHandler",
    "PreToolHook",
    "PreToolContext",
    "PostToolHook",
    "PostToolContext",
]
