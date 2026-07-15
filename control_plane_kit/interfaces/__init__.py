"""External interface adapters for control-plane-kit."""

from control_plane_kit.interfaces.mcp import McpToolDescriptor, ReadOnlyMcpAdapter

__all__ = [
    "McpToolDescriptor",
    "ReadOnlyMcpAdapter",
]
