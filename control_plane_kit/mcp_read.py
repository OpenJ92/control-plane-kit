"""MCP-shaped read-only adapter for a control-plane instance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from control_plane_kit.read_services import InstanceReadService, ReadModelError


class McpReadError(ValueError):
    """Raised when an MCP-shaped read request is invalid."""


@dataclass(frozen=True)
class McpToolDescriptor:
    """Small JSON-compatible descriptor for one read-only MCP tool."""

    name: str
    description: str
    input_schema: Mapping[str, object]

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


class ReadOnlyMcpAdapter:
    """Expose instance read models through MCP-shaped tool calls.

    This adapter does not depend on an MCP server runtime. It is the pure tool
    vocabulary and dispatch table that a runtime-specific MCP server can wrap.
    """

    def __init__(self, service: InstanceReadService) -> None:
        self._service = service
        self._tools: dict[str, Callable[[Mapping[str, object]], Mapping[str, object]]] = {
            "get_workspace": self._get_workspace,
            "get_current_graph": self._get_current_graph,
            "get_desired_graph": self._get_desired_graph,
            "get_operator_graph": self._get_operator_graph,
            "get_activity_timeline": self._get_activity_timeline,
            "get_observed_state": self._get_observed_state,
            "get_control_surface": self._get_control_surface,
        }

    def list_tools(self) -> tuple[Mapping[str, object], ...]:
        """Return deterministic read-only tool descriptors."""

        return tuple(_TOOL_DESCRIPTORS[name].descriptor() for name in sorted(self._tools))

    def call_tool(self, name: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """Call one read-only tool and return an MCP-shaped JSON payload."""

        try:
            handler = self._tools[name]
        except KeyError as exc:
            raise McpReadError(f"unknown read-only tool {name!r}") from exc
        try:
            payload = handler(arguments)
        except ReadModelError as exc:
            raise McpReadError(str(exc)) from exc
        return {
            "tool": name,
            "is_error": False,
            "content": [{"type": "json", "json": payload}],
        }

    def _get_workspace(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.workspace(_workspace_id(arguments)).descriptor()

    def _get_current_graph(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.current_graph(_workspace_id(arguments)).descriptor()

    def _get_desired_graph(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.desired_graph(_workspace_id(arguments)).descriptor()

    def _get_operator_graph(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.operator_graph(_workspace_id(arguments), pointer=_pointer(arguments)).descriptor()

    def _get_activity_timeline(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.activity_timeline(_workspace_id(arguments), limit=_limit(arguments)).descriptor()

    def _get_observed_state(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.observed_state(_workspace_id(arguments)).descriptor()

    def _get_control_surface(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        return self._service.control_surface(_workspace_id(arguments), pointer=_pointer(arguments)).descriptor()


def _workspace_id(arguments: Mapping[str, object]) -> str:
    value = arguments.get("workspace_id")
    if not isinstance(value, str) or not value:
        raise McpReadError("workspace_id is required")
    return value


def _pointer(arguments: Mapping[str, object]) -> str:
    value = arguments.get("pointer", "current")
    if not isinstance(value, str) or not value:
        raise McpReadError("pointer must be text")
    return value


def _limit(arguments: Mapping[str, object]) -> int:
    value = arguments.get("limit", 50)
    if not isinstance(value, int):
        raise McpReadError("limit must be an integer")
    return value


def _schema(*, pointer: bool = False, limit: bool = False) -> dict[str, object]:
    properties: dict[str, object] = {
        "workspace_id": {"type": "string"},
    }
    if pointer:
        properties["pointer"] = {"type": "string", "default": "current"}
    if limit:
        properties["limit"] = {"type": "integer", "default": 50, "minimum": 1}
    return {
        "type": "object",
        "required": ["workspace_id"],
        "properties": properties,
        "additionalProperties": False,
    }


_TOOL_DESCRIPTORS = {
    "get_workspace": McpToolDescriptor(
        "get_workspace",
        "Read one workspace summary and graph pointers.",
        _schema(),
    ),
    "get_current_graph": McpToolDescriptor(
        "get_current_graph",
        "Read the current graph descriptor for a workspace.",
        _schema(),
    ),
    "get_desired_graph": McpToolDescriptor(
        "get_desired_graph",
        "Read the desired graph descriptor for a workspace.",
        _schema(),
    ),
    "get_operator_graph": McpToolDescriptor(
        "get_operator_graph",
        "Read the operator-facing graph projection for a workspace.",
        _schema(pointer=True),
    ),
    "get_activity_timeline": McpToolDescriptor(
        "get_activity_timeline",
        "Read a bounded activity timeline for a workspace.",
        _schema(limit=True),
    ),
    "get_observed_state": McpToolDescriptor(
        "get_observed_state",
        "Read latest observed state for a workspace.",
        _schema(),
    ),
    "get_control_surface": McpToolDescriptor(
        "get_control_surface",
        "Read declared capabilities, control routes, and socket contracts.",
        _schema(pointer=True),
    ),
}
