"""Pure parity contracts across operator adapter surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from control_plane_kit_core.operations.http import (
    HttpApiContract,
    HttpOperationSafety,
)
from control_plane_kit_core.operations.mcp import McpStreamableHttpContract
from control_plane_kit_core.operations.services import ControlPlaneServiceRole


class InvalidAdapterParityContract(ValueError):
    """Raised when adapter parity is incoherent."""


@dataclass(frozen=True)
class AdapterProjectionBinding:
    """One canonical projection exposed through HTTP and MCP."""

    operation_id: str
    service_role: ControlPlaneServiceRole
    projection_schema: str
    http_route_id: str
    mcp_tool_name: str

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidAdapterParityContract(
                "service_role must be ControlPlaneServiceRole"
            )
        _validate_identity(self.projection_schema, "projection_schema")
        _validate_identity(self.http_route_id, "http_route_id")
        _validate_identity(self.mcp_tool_name, "mcp_tool_name")

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "service_role": self.service_role.value,
            "projection_schema": self.projection_schema,
            "http_route_id": self.http_route_id,
            "mcp_tool_name": self.mcp_tool_name,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "AdapterProjectionBinding":
        if set(value) != {
            "operation_id",
            "service_role",
            "projection_schema",
            "http_route_id",
            "mcp_tool_name",
        }:
            raise InvalidAdapterParityContract(
                "projection binding descriptor has unexpected keys"
            )
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                projection_schema=_text(
                    value["projection_schema"],
                    "projection_schema",
                ),
                http_route_id=_text(value["http_route_id"], "http_route_id"),
                mcp_tool_name=_text(value["mcp_tool_name"], "mcp_tool_name"),
            )
        except ValueError as error:
            raise InvalidAdapterParityContract(str(error)) from error


@dataclass(frozen=True)
class AdapterParityContract:
    """Prove HTTP and MCP expose one shared service/projection vocabulary."""

    http_api: HttpApiContract
    mcp: McpStreamableHttpContract
    projections: tuple[AdapterProjectionBinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.http_api, HttpApiContract):
            raise InvalidAdapterParityContract("http_api must be HttpApiContract")
        if not isinstance(self.mcp, McpStreamableHttpContract):
            raise InvalidAdapterParityContract("mcp must be McpStreamableHttpContract")
        if not isinstance(self.projections, tuple) or not all(
            isinstance(binding, AdapterProjectionBinding)
            for binding in self.projections
        ):
            raise InvalidAdapterParityContract(
                "projections must be AdapterProjectionBinding values"
            )
        _reject_duplicates(
            "operation_id",
            (binding.operation_id for binding in self.projections),
        )
        _reject_duplicates(
            "http_route_id",
            (binding.http_route_id for binding in self.projections),
        )
        _reject_duplicates(
            "mcp_tool_name",
            (binding.mcp_tool_name for binding in self.projections),
        )
        for binding in self.projections:
            route = self.http_api.route(binding.http_route_id)
            if route.service_role is not binding.service_role:
                raise InvalidAdapterParityContract(
                    f"{binding.operation_id!r} service role does not match HTTP route"
                )
            if route.response_schema.name != binding.projection_schema:
                raise InvalidAdapterParityContract(
                    f"{binding.operation_id!r} projection schema does not match HTTP route"
                )
            if (
                binding.service_role is ControlPlaneServiceRole.READS
                and route.safety is not HttpOperationSafety.READ_ONLY
            ):
                raise InvalidAdapterParityContract(
                    f"{binding.operation_id!r} read projection must use read-only route"
                )
        ordered = tuple(sorted(self.projections, key=lambda binding: binding.operation_id))
        object.__setattr__(self, "projections", ordered)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "adapter-parity-contract",
            "http_api": self.http_api.descriptor(),
            "mcp": self.mcp.descriptor(),
            "projections": [
                binding.descriptor() for binding in self.projections
            ],
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "AdapterParityContract":
        if set(value) != {"kind", "http_api", "mcp", "projections"}:
            raise InvalidAdapterParityContract(
                "adapter parity descriptor has unexpected keys"
            )
        if value["kind"] != "adapter-parity-contract":
            raise InvalidAdapterParityContract("adapter parity descriptor has wrong kind")
        projections = value["projections"]
        if not isinstance(projections, list):
            raise InvalidAdapterParityContract("projections must be a list")
        try:
            return cls(
                http_api=HttpApiContract.from_descriptor(
                    _mapping(value["http_api"], "http_api")
                ),
                mcp=McpStreamableHttpContract.from_descriptor(
                    _mapping(value["mcp"], "mcp")
                ),
                projections=tuple(
                    AdapterProjectionBinding.from_descriptor(
                        _mapping(projection, "projection")
                    )
                    for projection in projections
                ),
            )
        except ValueError as error:
            raise InvalidAdapterParityContract(str(error)) from error


def operator_read_projection_parity(
    http_api: HttpApiContract,
    mcp: McpStreamableHttpContract,
) -> AdapterParityContract:
    """Bind the frozen read route inventory to the MCP read tool vocabulary."""

    return AdapterParityContract(
        http_api=http_api,
        mcp=mcp,
        projections=tuple(
            AdapterProjectionBinding(
                operation_id=operation_id,
                service_role=ControlPlaneServiceRole.READS,
                projection_schema=projection_schema,
                http_route_id=http_route_id,
                mcp_tool_name=mcp_tool_name,
            )
            for (
                operation_id,
                http_route_id,
                mcp_tool_name,
                projection_schema,
            ) in _OPERATOR_READ_PROJECTIONS
        ),
    )


_OPERATOR_READ_PROJECTIONS = (
    (
        "read.workspace",
        "read.workspace",
        "get_workspace",
        "WorkspaceReadResponse",
    ),
    (
        "read.current-graph",
        "read.current-graph",
        "get_current_graph",
        "GraphReadResponse",
    ),
    (
        "read.desired-graph",
        "read.desired-graph",
        "get_desired_graph",
        "GraphReadResponse",
    ),
    (
        "read.operator-graph",
        "read.operator-graph",
        "get_operator_graph",
        "OperatorGraphReadResponse",
    ),
    (
        "read.activity-timeline",
        "read.activity",
        "get_activity_timeline",
        "ActivityTimelineReadResponse",
    ),
    (
        "read.open-sessions",
        "read.sessions",
        "list_open_sessions",
        "OpenSessionsReadResponse",
    ),
    (
        "read.session-detail",
        "read.session-detail",
        "get_session_detail",
        "SessionDetailReadResponse",
    ),
    (
        "read.plan-detail",
        "read.plan-detail",
        "get_plan_detail",
        "PlanDetailReadResponse",
    ),
    (
        "read.pending-approvals",
        "read.pending-approvals",
        "list_pending_approvals",
        "PendingApprovalsReadResponse",
    ),
    (
        "read.observed-state",
        "read.observed-state",
        "get_observed_state",
        "ObservedStateReadResponse",
    ),
    (
        "read.control-surface",
        "read.control-surface",
        "get_control_surface",
        "ControlSurfaceReadResponse",
    ),
)


def _reject_duplicates(field: str, values: object) -> None:
    sequence = tuple(values)
    if len(sequence) != len(set(sequence)):
        raise InvalidAdapterParityContract(f"{field} values must be unique")


def _validate_identity(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidAdapterParityContract(f"{field} must be non-empty text")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(character not in allowed for character in value):
        raise InvalidAdapterParityContract(
            f"{field} must contain only letters, numbers, dots, dashes, or underscores"
        )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidAdapterParityContract(f"{field} must be text")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidAdapterParityContract(f"{field} must be a descriptor")
    return value
