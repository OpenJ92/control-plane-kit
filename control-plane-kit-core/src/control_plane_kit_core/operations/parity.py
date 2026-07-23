"""Pure parity contracts across operator adapter surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.http import (
    HttpApiContract,
    HttpAuthScope,
    HttpOperationSafety,
)
from control_plane_kit_core.operations.mcp import McpStreamableHttpContract
from control_plane_kit_core.operations.services import ControlPlaneServiceRole
from control_plane_kit_core.operations.transactions import (
    ExternalEffectPolicy,
    StoreParticipation,
    UnitOfWorkBoundary,
)


class InvalidAdapterParityContract(ValueError):
    """Raised when adapter parity is incoherent."""


class CommandIdempotencyPolicy(StrEnum):
    """Closed command retry and duplicate-request policy."""

    REQUIRED = "required"
    BEST_EFFORT = "best-effort"


class ApprovalPolicy(StrEnum):
    """Closed command approval relation at the shared service boundary."""

    NOT_REQUIRED = "not-required"
    SUBMITS_FOR_APPROVAL = "submits-for-approval"
    DECIDES_APPROVAL = "decides-approval"
    REQUIRES_CURRENT_APPROVAL = "requires-current-approval"


class ActivityHistoryPolicy(StrEnum):
    """Closed activity-history evidence requirement for adapter operations."""

    NOT_RECORDED = "not-recorded"
    RECORD_ACCEPTED_AND_REJECTED_COMMANDS = "record-accepted-and-rejected-commands"


class ErrorDisclosurePolicy(StrEnum):
    """Closed error disclosure policy shared by adapter transports."""

    BOUNDED_REDACTED = "bounded-redacted"
    TRANSPORT_PRIVATE = "transport-private"


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
class AdapterCommandBinding:
    """One canonical command exposed through HTTP and MCP."""

    operation_id: str
    service_role: ControlPlaneServiceRole
    request_schema: str
    response_schema: str
    http_route_id: str
    mcp_tool_name: str
    idempotency: CommandIdempotencyPolicy
    approval: ApprovalPolicy

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidAdapterParityContract(
                "service_role must be ControlPlaneServiceRole"
            )
        _validate_identity(self.request_schema, "request_schema")
        _validate_identity(self.response_schema, "response_schema")
        _validate_identity(self.http_route_id, "http_route_id")
        _validate_identity(self.mcp_tool_name, "mcp_tool_name")
        if not isinstance(self.idempotency, CommandIdempotencyPolicy):
            raise InvalidAdapterParityContract(
                "idempotency must be CommandIdempotencyPolicy"
            )
        if not isinstance(self.approval, ApprovalPolicy):
            raise InvalidAdapterParityContract("approval must be ApprovalPolicy")

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "service_role": self.service_role.value,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
            "http_route_id": self.http_route_id,
            "mcp_tool_name": self.mcp_tool_name,
            "idempotency": self.idempotency.value,
            "approval": self.approval.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "AdapterCommandBinding":
        if set(value) != {
            "operation_id",
            "service_role",
            "request_schema",
            "response_schema",
            "http_route_id",
            "mcp_tool_name",
            "idempotency",
            "approval",
        }:
            raise InvalidAdapterParityContract(
                "command binding descriptor has unexpected keys"
            )
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                request_schema=_text(value["request_schema"], "request_schema"),
                response_schema=_text(value["response_schema"], "response_schema"),
                http_route_id=_text(value["http_route_id"], "http_route_id"),
                mcp_tool_name=_text(value["mcp_tool_name"], "mcp_tool_name"),
                idempotency=CommandIdempotencyPolicy(
                    _text(value["idempotency"], "idempotency")
                ),
                approval=ApprovalPolicy(_text(value["approval"], "approval")),
            )
        except ValueError as error:
            raise InvalidAdapterParityContract(str(error)) from error


@dataclass(frozen=True)
class AdapterOperationSecurityBinding:
    """Authorization, history, and redaction law for one adapter operation."""

    operation_id: str
    service_role: ControlPlaneServiceRole
    http_route_id: str
    mcp_name: str
    auth_scope: HttpAuthScope
    safety: HttpOperationSafety
    activity_history: ActivityHistoryPolicy
    error_disclosure: ErrorDisclosurePolicy

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidAdapterParityContract(
                "service_role must be ControlPlaneServiceRole"
            )
        _validate_identity(self.http_route_id, "http_route_id")
        _validate_identity(self.mcp_name, "mcp_name")
        if not isinstance(self.auth_scope, HttpAuthScope):
            raise InvalidAdapterParityContract("auth_scope must be HttpAuthScope")
        if not isinstance(self.safety, HttpOperationSafety):
            raise InvalidAdapterParityContract("safety must be HttpOperationSafety")
        if not isinstance(self.activity_history, ActivityHistoryPolicy):
            raise InvalidAdapterParityContract(
                "activity_history must be ActivityHistoryPolicy"
            )
        if not isinstance(self.error_disclosure, ErrorDisclosurePolicy):
            raise InvalidAdapterParityContract(
                "error_disclosure must be ErrorDisclosurePolicy"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "service_role": self.service_role.value,
            "http_route_id": self.http_route_id,
            "mcp_name": self.mcp_name,
            "auth_scope": self.auth_scope.value,
            "safety": self.safety.value,
            "activity_history": self.activity_history.value,
            "error_disclosure": self.error_disclosure.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "AdapterOperationSecurityBinding":
        if set(value) != {
            "operation_id",
            "service_role",
            "http_route_id",
            "mcp_name",
            "auth_scope",
            "safety",
            "activity_history",
            "error_disclosure",
        }:
            raise InvalidAdapterParityContract(
                "operation security descriptor has unexpected keys"
            )
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                http_route_id=_text(value["http_route_id"], "http_route_id"),
                mcp_name=_text(value["mcp_name"], "mcp_name"),
                auth_scope=HttpAuthScope(_text(value["auth_scope"], "auth_scope")),
                safety=HttpOperationSafety(_text(value["safety"], "safety")),
                activity_history=ActivityHistoryPolicy(
                    _text(value["activity_history"], "activity_history")
                ),
                error_disclosure=ErrorDisclosurePolicy(
                    _text(value["error_disclosure"], "error_disclosure")
                ),
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


@dataclass(frozen=True)
class AdapterCommandParityContract:
    """Prove HTTP and MCP expose one shared command-policy vocabulary."""

    http_api: HttpApiContract
    mcp: McpStreamableHttpContract
    unit_of_work: UnitOfWorkBoundary
    commands: tuple[AdapterCommandBinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.http_api, HttpApiContract):
            raise InvalidAdapterParityContract("http_api must be HttpApiContract")
        if not isinstance(self.mcp, McpStreamableHttpContract):
            raise InvalidAdapterParityContract("mcp must be McpStreamableHttpContract")
        if not isinstance(self.unit_of_work, UnitOfWorkBoundary):
            raise InvalidAdapterParityContract("unit_of_work must be UnitOfWorkBoundary")
        if not isinstance(self.commands, tuple) or not all(
            isinstance(binding, AdapterCommandBinding)
            for binding in self.commands
        ):
            raise InvalidAdapterParityContract(
                "commands must be AdapterCommandBinding values"
            )
        _reject_duplicates(
            "operation_id",
            (binding.operation_id for binding in self.commands),
        )
        _reject_duplicates(
            "http_route_id",
            (binding.http_route_id for binding in self.commands),
        )
        _reject_duplicates(
            "mcp_tool_name",
            (binding.mcp_tool_name for binding in self.commands),
        )
        for binding in self.commands:
            self._validate_binding(binding)
        ordered = tuple(sorted(self.commands, key=lambda binding: binding.operation_id))
        object.__setattr__(self, "commands", ordered)

    def _validate_binding(self, binding: AdapterCommandBinding) -> None:
        route = self.http_api.route(binding.http_route_id)
        if route.service_role is not binding.service_role:
            raise InvalidAdapterParityContract(
                f"{binding.operation_id!r} service role does not match HTTP route"
            )
        if route.request_schema.name != binding.request_schema:
            raise InvalidAdapterParityContract(
                f"{binding.operation_id!r} request schema does not match HTTP route"
            )
        if route.response_schema.name != binding.response_schema:
            raise InvalidAdapterParityContract(
                f"{binding.operation_id!r} response schema does not match HTTP route"
            )
        if route.safety is HttpOperationSafety.READ_ONLY:
            raise InvalidAdapterParityContract(
                f"{binding.operation_id!r} command must not use read-only route"
            )

        boundary = self.unit_of_work.service(binding.service_role)
        if boundary.store_participation is not StoreParticipation.READ_WRITE:
            raise InvalidAdapterParityContract(
                f"{binding.operation_id!r} command service must be read-write"
            )
        if not boundary.owns_transaction:
            raise InvalidAdapterParityContract(
                f"{binding.operation_id!r} command service must own transaction"
            )
        if route.safety is HttpOperationSafety.DESTRUCTIVE:
            if binding.idempotency is not CommandIdempotencyPolicy.REQUIRED:
                raise InvalidAdapterParityContract(
                    f"{binding.operation_id!r} destructive command requires idempotency"
                )
            if binding.approval is not ApprovalPolicy.REQUIRES_CURRENT_APPROVAL:
                raise InvalidAdapterParityContract(
                    f"{binding.operation_id!r} destructive command requires current approval"
                )
            if (
                boundary.external_effect_policy
                is not ExternalEffectPolicy.AFTER_COMMIT
            ):
                raise InvalidAdapterParityContract(
                    f"{binding.operation_id!r} external effects must occur after commit"
                )
        if (
            binding.approval is ApprovalPolicy.REQUIRES_CURRENT_APPROVAL
            and binding.idempotency is not CommandIdempotencyPolicy.REQUIRED
        ):
            raise InvalidAdapterParityContract(
                f"{binding.operation_id!r} approval-gated command requires idempotency"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "adapter-command-parity-contract",
            "http_api": self.http_api.descriptor(),
            "mcp": self.mcp.descriptor(),
            "unit_of_work": self.unit_of_work.descriptor(),
            "commands": [binding.descriptor() for binding in self.commands],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "AdapterCommandParityContract":
        if set(value) != {"kind", "http_api", "mcp", "unit_of_work", "commands"}:
            raise InvalidAdapterParityContract(
                "adapter command parity descriptor has unexpected keys"
            )
        if value["kind"] != "adapter-command-parity-contract":
            raise InvalidAdapterParityContract(
                "adapter command parity descriptor has wrong kind"
            )
        commands = value["commands"]
        if not isinstance(commands, list):
            raise InvalidAdapterParityContract("commands must be a list")
        try:
            return cls(
                http_api=HttpApiContract.from_descriptor(
                    _mapping(value["http_api"], "http_api")
                ),
                mcp=McpStreamableHttpContract.from_descriptor(
                    _mapping(value["mcp"], "mcp")
                ),
                unit_of_work=UnitOfWorkBoundary.from_descriptor(
                    _mapping(value["unit_of_work"], "unit_of_work")
                ),
                commands=tuple(
                    AdapterCommandBinding.from_descriptor(
                        _mapping(command, "command")
                    )
                    for command in commands
                ),
            )
        except ValueError as error:
            raise InvalidAdapterParityContract(str(error)) from error


@dataclass(frozen=True)
class AdapterOperationSecurityParityContract:
    """Prove auth, safety, activity, and error laws across adapters."""

    projection_parity: AdapterParityContract
    command_parity: AdapterCommandParityContract
    operations: tuple[AdapterOperationSecurityBinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.projection_parity, AdapterParityContract):
            raise InvalidAdapterParityContract(
                "projection_parity must be AdapterParityContract"
            )
        if not isinstance(self.command_parity, AdapterCommandParityContract):
            raise InvalidAdapterParityContract(
                "command_parity must be AdapterCommandParityContract"
            )
        if self.projection_parity.mcp != self.command_parity.mcp:
            raise InvalidAdapterParityContract(
                "projection and command parity must use one MCP contract"
            )
        if not isinstance(self.operations, tuple) or not all(
            isinstance(operation, AdapterOperationSecurityBinding)
            for operation in self.operations
        ):
            raise InvalidAdapterParityContract(
                "operations must be AdapterOperationSecurityBinding values"
            )
        _reject_duplicates(
            "operation_id",
            (operation.operation_id for operation in self.operations),
        )
        expected = {
            binding.operation_id
            for binding in self.projection_parity.projections
        } | {
            binding.operation_id
            for binding in self.command_parity.commands
        }
        actual = {operation.operation_id for operation in self.operations}
        if actual != expected:
            raise InvalidAdapterParityContract(
                "operation security parity must cover every read and command operation"
            )
        projection_by_id = {
            binding.operation_id: binding
            for binding in self.projection_parity.projections
        }
        command_by_id = {
            binding.operation_id: binding
            for binding in self.command_parity.commands
        }
        for operation in self.operations:
            if operation.operation_id in projection_by_id:
                self._validate_projection(operation, projection_by_id[operation.operation_id])
            elif operation.operation_id in command_by_id:
                self._validate_command(operation, command_by_id[operation.operation_id])
            if operation.error_disclosure is not ErrorDisclosurePolicy.BOUNDED_REDACTED:
                raise InvalidAdapterParityContract(
                    f"{operation.operation_id!r} errors must be bounded and redacted"
                )
        ordered = tuple(
            sorted(self.operations, key=lambda operation: operation.operation_id)
        )
        object.__setattr__(self, "operations", ordered)

    def _validate_projection(
        self,
        operation: AdapterOperationSecurityBinding,
        binding: AdapterProjectionBinding,
    ) -> None:
        route = self.projection_parity.http_api.route(binding.http_route_id)
        if operation.service_role is not binding.service_role:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} projection service role mismatch"
            )
        if operation.http_route_id != binding.http_route_id:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} projection HTTP route mismatch"
            )
        if operation.mcp_name != binding.mcp_tool_name:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} projection MCP name mismatch"
            )
        if operation.auth_scope is not route.auth_scope:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} projection auth scope mismatch"
            )
        if operation.auth_scope is not HttpAuthScope.READ:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} read projection requires read auth"
            )
        if operation.safety is not HttpOperationSafety.READ_ONLY:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} read projection must be read-only"
            )
        if operation.activity_history is not ActivityHistoryPolicy.NOT_RECORDED:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} read projection must not claim command history"
            )

    def _validate_command(
        self,
        operation: AdapterOperationSecurityBinding,
        binding: AdapterCommandBinding,
    ) -> None:
        route = self.command_parity.http_api.route(binding.http_route_id)
        if operation.service_role is not binding.service_role:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} command service role mismatch"
            )
        if operation.http_route_id != binding.http_route_id:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} command HTTP route mismatch"
            )
        if operation.mcp_name != binding.mcp_tool_name:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} command MCP name mismatch"
            )
        if operation.auth_scope is not route.auth_scope:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} command auth scope mismatch"
            )
        if operation.auth_scope is HttpAuthScope.READ:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} command must not use read auth"
            )
        if operation.safety is not route.safety:
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} command safety mismatch"
            )
        if (
            operation.activity_history
            is not ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS
        ):
            raise InvalidAdapterParityContract(
                f"{operation.operation_id!r} accepted and rejected commands require activity history"
            )

    def operation(
        self,
        operation_id: str,
    ) -> AdapterOperationSecurityBinding:
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation
        raise InvalidAdapterParityContract(f"unknown operation_id {operation_id!r}")

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "adapter-operation-security-parity",
            "projection_parity": self.projection_parity.descriptor(),
            "command_parity": self.command_parity.descriptor(),
            "operations": [
                operation.descriptor() for operation in self.operations
            ],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "AdapterOperationSecurityParityContract":
        if set(value) != {
            "kind",
            "projection_parity",
            "command_parity",
            "operations",
        }:
            raise InvalidAdapterParityContract(
                "adapter operation security descriptor has unexpected keys"
            )
        if value["kind"] != "adapter-operation-security-parity":
            raise InvalidAdapterParityContract(
                "adapter operation security descriptor has wrong kind"
            )
        operations = value["operations"]
        if not isinstance(operations, list):
            raise InvalidAdapterParityContract("operations must be a list")
        try:
            return cls(
                projection_parity=AdapterParityContract.from_descriptor(
                    _mapping(value["projection_parity"], "projection_parity")
                ),
                command_parity=AdapterCommandParityContract.from_descriptor(
                    _mapping(value["command_parity"], "command_parity")
                ),
                operations=tuple(
                    AdapterOperationSecurityBinding.from_descriptor(
                        _mapping(operation, "operation")
                    )
                    for operation in operations
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


def operator_adapter_security_parity(
    *,
    projection_parity: AdapterParityContract,
    command_parity: AdapterCommandParityContract,
) -> AdapterOperationSecurityParityContract:
    """Bind auth, safety, history, and error policy across adapter surfaces."""

    projection_operations = tuple(
        AdapterOperationSecurityBinding(
            operation_id=binding.operation_id,
            service_role=binding.service_role,
            http_route_id=binding.http_route_id,
            mcp_name=binding.mcp_tool_name,
            auth_scope=projection_parity.http_api.route(
                binding.http_route_id
            ).auth_scope,
            safety=HttpOperationSafety.READ_ONLY,
            activity_history=ActivityHistoryPolicy.NOT_RECORDED,
            error_disclosure=ErrorDisclosurePolicy.BOUNDED_REDACTED,
        )
        for binding in projection_parity.projections
    )
    command_operations = tuple(
        AdapterOperationSecurityBinding(
            operation_id=binding.operation_id,
            service_role=binding.service_role,
            http_route_id=binding.http_route_id,
            mcp_name=binding.mcp_tool_name,
            auth_scope=command_parity.http_api.route(
                binding.http_route_id
            ).auth_scope,
            safety=command_parity.http_api.route(binding.http_route_id).safety,
            activity_history=(
                ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS
            ),
            error_disclosure=ErrorDisclosurePolicy.BOUNDED_REDACTED,
        )
        for binding in command_parity.commands
    )
    return AdapterOperationSecurityParityContract(
        projection_parity=projection_parity,
        command_parity=command_parity,
        operations=projection_operations + command_operations,
    )


def operator_command_parity(
    http_api: HttpApiContract,
    mcp: McpStreamableHttpContract,
    unit_of_work: UnitOfWorkBoundary,
) -> AdapterCommandParityContract:
    """Bind operator command routes to MCP tools and service policy."""

    return AdapterCommandParityContract(
        http_api=http_api,
        mcp=mcp,
        unit_of_work=unit_of_work,
        commands=tuple(
            AdapterCommandBinding(
                operation_id=operation_id,
                service_role=service_role,
                request_schema=request_schema,
                response_schema=response_schema,
                http_route_id=http_route_id,
                mcp_tool_name=mcp_tool_name,
                idempotency=CommandIdempotencyPolicy.REQUIRED,
                approval=approval,
            )
            for (
                operation_id,
                http_route_id,
                mcp_tool_name,
                service_role,
                request_schema,
                response_schema,
                approval,
            ) in _OPERATOR_COMMANDS
        ),
    )


_OPERATOR_READ_PROJECTIONS = (
    (
        "read.approval-detail",
        "read.approval-detail",
        "get_approval_detail",
        "ApprovalDetailReadResponse",
    ),
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


_OPERATOR_COMMANDS = (
    (
        "workspace.create",
        "command.workspace.create",
        "create_workspace",
        ControlPlaneServiceRole.PLANNING,
        "CreateWorkspaceRequest",
        "WorkspaceReadResponse",
        ApprovalPolicy.NOT_REQUIRED,
    ),
    (
        "product-descriptor.import",
        "command.product.import",
        "import_product_descriptor",
        ControlPlaneServiceRole.PLANNING,
        "ImportProductDescriptorRequest",
        "RegisteredProductResponse",
        ApprovalPolicy.NOT_REQUIRED,
    ),
    (
        "operation-session.start",
        "command.operation-session.start",
        "start_operation_session",
        ControlPlaneServiceRole.LIFECYCLE,
        "StartOperationSessionRequest",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
    ),
    (
        "operation-session.close",
        "command.operation-session.close",
        "close_operation_session",
        ControlPlaneServiceRole.LIFECYCLE,
        "CloseOperationSessionRequest",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
    ),
    (
        "operation-session.cancel",
        "command.operation-session.cancel",
        "cancel_operation_session",
        ControlPlaneServiceRole.LIFECYCLE,
        "CancelOperationSessionRequest",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
    ),
    (
        "operation-session.record-action",
        "command.operation-session.record-action",
        "record_operation_action",
        ControlPlaneServiceRole.LIFECYCLE,
        "RecordOperationActionRequest",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
    ),
    (
        "desired-graph.set",
        "command.desired-graph.set",
        "set_desired_graph",
        ControlPlaneServiceRole.PLANNING,
        "SetDesiredGraphRequest",
        "DesiredGraphEditResult",
        ApprovalPolicy.SUBMITS_FOR_APPROVAL,
    ),
    (
        "deployment.plan",
        "command.deployment.plan",
        "plan_deployment",
        ControlPlaneServiceRole.PLANNING,
        "PlanDeploymentRequest",
        "PlanDeploymentResponse",
        ApprovalPolicy.SUBMITS_FOR_APPROVAL,
    ),
    (
        "approval.decide",
        "command.approval.decide",
        "decide_approval",
        ControlPlaneServiceRole.APPROVAL,
        "ApprovalDecisionRequest",
        "ApprovalDecisionResponse",
        ApprovalPolicy.DECIDES_APPROVAL,
    ),
    (
        "approval.request",
        "command.approval.request",
        "request_approval",
        ControlPlaneServiceRole.APPROVAL,
        "ApprovalRequestRequest",
        "ApprovalRequestResponse",
        ApprovalPolicy.SUBMITS_FOR_APPROVAL,
    ),
    (
        "deployment.admit",
        "command.deployment.admit",
        "admit_deployment",
        ControlPlaneServiceRole.ADMISSION,
        "AdmitDeploymentRequest",
        "AdmittedRunResponse",
        ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
    ),
    (
        "run.claim",
        "command.run.claim",
        "claim_run",
        ControlPlaneServiceRole.LIFECYCLE,
        "ClaimRunRequest",
        "ClaimRunResponse",
        ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
    ),
    (
        "run.start",
        "command.run.start",
        "start_run",
        ControlPlaneServiceRole.EXECUTION,
        "StartRunRequest",
        "ActivityRunTransitionResult",
        ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
    ),
    (
        "deployment.execute",
        "command.deployment.execute",
        "execute_deployment",
        ControlPlaneServiceRole.EXECUTION,
        "ExecuteDeploymentRequest",
        "ExecutionRunResponse",
        ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
    ),
    (
        "graph.advance-current",
        "command.graph.advance-current",
        "advance_current_graph",
        ControlPlaneServiceRole.LIFECYCLE,
        "AdvanceCurrentGraphRequest",
        "CurrentGraphAdvancementResult",
        ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
    ),
    (
        "recovery.decide",
        "command.recovery.decide",
        "decide_recovery",
        ControlPlaneServiceRole.RECOVERY,
        "RecoveryDecisionRequest",
        "RecoveryDecisionResponse",
        ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
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
