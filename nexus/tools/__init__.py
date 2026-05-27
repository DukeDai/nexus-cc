"""Nexus Tools Package — auto-discovered by ToolRegistry.

Tools in this package are auto-loaded by ToolRegistry.register_all('nexus.tools').
Each tool must subclass src.engine.registry.BaseTool.

Example:
    from src.engine.registry import BaseTool
    from src.tools.base import ToolResult, ToolStatus

    class MyTool(BaseTool):
        name = "my_tool"
        description = "Does something useful"
        input_schema = {
            "type": "object",
            "properties": {"arg": {"type": "string"}},
            "required": ["arg"]
        }

        def __call__(self, **kwargs) -> ToolResult:
            # your logic here
            return ToolResult(message="done", success=True)

__all__ = []
"""
