"""MCP-shaped read-only adapter for instance projections.

This module deliberately avoids depending on a concrete MCP SDK. It defines the
tool names, input schemas, and dispatch behavior that a real MCP server can
wrap later. Keeping this layer transport-agnostic lets Roadmap 0006 provide a
stable read vocabulary without pretending runtime/server concerns are solved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from control_plane_kit.read_services import InstanceReadService


class DescriptorReadModel(Protocol):
    """Read model shape returned by the instance read service."""

    def descriptor(self) -> dict[str, object]:
        """Return the JSON-ready descriptor for this read model."""


@dataclass(frozen=True)
class McpToolDescriptor:
    """One read-only tool exposed to an MCP transport."""

    name: str
    description: str
    input_schema: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


class ReadOnlyMcpAdapter:
    """Transport-agnostic read-only MCP tool adapter."""

    def __init__(self, read_service: InstanceReadService) -> None:
        self._read_service = read_service

    def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        """Return supported read-only tool descriptors."""

        return (
            McpToolDescriptor(
                "get_workspace",
                "Read one workspace summary.",
                _workspace_schema(),
            ),
            McpToolDescriptor(
                "get_current_graph",
                "Read the current graph projection for a workspace.",
                _workspace_schema(),
            ),
            McpToolDescriptor(
                "get_desired_graph",
                "Read the desired graph projection for a workspace.",
                _workspace_schema(),
            ),
            McpToolDescriptor(
                "get_activity_timeline",
                "Read a bounded activity timeline for a workspace.",
                _workspace_limit_schema(default=50),
            ),
            McpToolDescriptor(
                "get_observed_state",
                "Read latest observed state summaries for a workspace.",
                _workspace_limit_schema(default=100),
            ),
            McpToolDescriptor(
                "get_control_surface",
                "Read declared capabilities, contracts, and route surfaces for a workspace.",
                _workspace_schema(),
            ),
        )

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        """Call a read-only tool and return a descriptor payload."""

        workspace_id = _workspace_id(arguments)
        match name:
            case "get_workspace":
                return self._read_service.workspace(workspace_id).descriptor()
            case "get_current_graph":
                return _assigned_descriptor(
                    self._read_service.current_graph(workspace_id),
                    tool_name=name,
                    workspace_id=workspace_id,
                )
            case "get_desired_graph":
                return _assigned_descriptor(
                    self._read_service.desired_graph(workspace_id),
                    tool_name=name,
                    workspace_id=workspace_id,
                )
            case "get_activity_timeline":
                return self._read_service.activity_timeline(
                    workspace_id,
                    limit=_limit(arguments, default=50),
                ).descriptor()
            case "get_observed_state":
                return self._read_service.observed_state(
                    workspace_id,
                    limit=_limit(arguments, default=100),
                ).descriptor()
            case "get_control_surface":
                return _assigned_descriptor(
                    self._read_service.control_surface(workspace_id),
                    tool_name=name,
                    workspace_id=workspace_id,
                )
            case _:
                raise KeyError(f"unknown read-only MCP tool {name!r}")


def _assigned_descriptor(
    read_model: DescriptorReadModel | None,
    *,
    tool_name: str,
    workspace_id: str,
) -> dict[str, object]:
    if read_model is None:
        raise KeyError(f"{tool_name} is not assigned for workspace {workspace_id!r}")
    return read_model.descriptor()


def _workspace_id(arguments: Mapping[str, object]) -> str:
    workspace_id = arguments.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("workspace_id is required")
    return workspace_id


def _limit(arguments: Mapping[str, object], *, default: int) -> int:
    value = arguments.get("limit", default)
    if not isinstance(value, int) or value < 1:
        raise ValueError("limit must be a positive integer")
    return value


def _workspace_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
        },
        "required": ["workspace_id"],
        "additionalProperties": False,
    }


def _workspace_limit_schema(*, default: int) -> dict[str, object]:
    schema = _workspace_schema()
    schema["properties"] = {
        **dict(schema["properties"]),
        "limit": {"type": "integer", "minimum": 1, "default": default},
    }
    return schema
