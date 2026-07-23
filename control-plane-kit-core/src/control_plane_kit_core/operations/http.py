"""Pure HTTP API route and schema contract values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.services import ControlPlaneServiceRole


class InvalidHttpApiContract(ValueError):
    """Raised when an HTTP API contract is incoherent."""


class HttpMethod(StrEnum):
    """Closed HTTP methods supported by control-plane API contracts."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class HttpAuthScope(StrEnum):
    """Closed authorization scopes for operator HTTP API routes."""

    READ = "read"
    PLAN_WRITE = "plan:write"
    APPROVAL_DECIDE = "approval:decide"
    EXECUTION_RUN = "execution:run"
    ADMIN = "admin"


class HttpOperationSafety(StrEnum):
    """Closed route safety classification for review and authorization."""

    READ_ONLY = "read-only"
    COMMAND = "command"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class HttpSchemaRef:
    """Bounded named request or response schema reference."""

    name: str
    max_bytes: int = 65536

    def __post_init__(self) -> None:
        _validate_identity(self.name, "schema name")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 1048576:
            raise InvalidHttpApiContract(
                "schema max_bytes must be an integer from 1 through 1048576"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "max_bytes": self.max_bytes,
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "HttpSchemaRef":
        if set(value) != {"name", "max_bytes"}:
            raise InvalidHttpApiContract("schema descriptor has unexpected keys")
        name = value["name"]
        max_bytes = value["max_bytes"]
        if not isinstance(name, str):
            raise InvalidHttpApiContract("schema name must be text")
        if type(max_bytes) is not int:
            raise InvalidHttpApiContract("schema max_bytes must be an integer")
        return cls(name, max_bytes=max_bytes)


@dataclass(frozen=True)
class HttpErrorContract:
    """Bounded error payload contract shared by operator HTTP routes."""

    statuses: tuple[int, ...] = (400, 401, 403, 404, 409, 422, 503)
    schema: HttpSchemaRef = field(
        default_factory=lambda: HttpSchemaRef("BoundedError", max_bytes=8192)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.statuses, tuple) or not all(
            type(status) is int for status in self.statuses
        ):
            raise InvalidHttpApiContract("error statuses must be integers")
        if tuple(sorted(set(self.statuses))) != self.statuses:
            raise InvalidHttpApiContract("error statuses must be unique and sorted")
        if not all(400 <= status <= 599 for status in self.statuses):
            raise InvalidHttpApiContract("error statuses must be 4xx or 5xx")
        if not isinstance(self.schema, HttpSchemaRef):
            raise InvalidHttpApiContract("error schema must be HttpSchemaRef")

    def descriptor(self) -> dict[str, object]:
        return {
            "statuses": list(self.statuses),
            "schema": self.schema.descriptor(),
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "HttpErrorContract":
        if set(value) != {"statuses", "schema"}:
            raise InvalidHttpApiContract("error descriptor has unexpected keys")
        statuses = value["statuses"]
        schema = value["schema"]
        if not isinstance(statuses, list):
            raise InvalidHttpApiContract("error statuses must be a list")
        if not isinstance(schema, Mapping):
            raise InvalidHttpApiContract("error schema must be a descriptor")
        return cls(
            statuses=tuple(statuses),
            schema=HttpSchemaRef.from_descriptor(schema),
        )


@dataclass(frozen=True)
class HttpApiRouteContract:
    """Pure route contract over one control-plane application service."""

    route_id: str
    method: HttpMethod
    path_template: str
    service_role: ControlPlaneServiceRole
    auth_scope: HttpAuthScope
    safety: HttpOperationSafety
    request_schema: HttpSchemaRef = field(
        default_factory=lambda: HttpSchemaRef("EmptyRequest", max_bytes=1024)
    )
    response_schema: HttpSchemaRef = field(
        default_factory=lambda: HttpSchemaRef("JsonResponse")
    )
    errors: HttpErrorContract = field(default_factory=HttpErrorContract)

    def __post_init__(self) -> None:
        _validate_identity(self.route_id, "route_id")
        if not isinstance(self.method, HttpMethod):
            raise InvalidHttpApiContract("route method must be HttpMethod")
        _validate_path_template(self.path_template)
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidHttpApiContract(
                "route service_role must be ControlPlaneServiceRole"
            )
        if not isinstance(self.auth_scope, HttpAuthScope):
            raise InvalidHttpApiContract("route auth_scope must be HttpAuthScope")
        if not isinstance(self.safety, HttpOperationSafety):
            raise InvalidHttpApiContract("route safety must be HttpOperationSafety")
        if not isinstance(self.request_schema, HttpSchemaRef):
            raise InvalidHttpApiContract("request_schema must be HttpSchemaRef")
        if not isinstance(self.response_schema, HttpSchemaRef):
            raise InvalidHttpApiContract("response_schema must be HttpSchemaRef")
        if not isinstance(self.errors, HttpErrorContract):
            raise InvalidHttpApiContract("errors must be HttpErrorContract")

        if self.safety is HttpOperationSafety.READ_ONLY:
            if self.method is not HttpMethod.GET:
                raise InvalidHttpApiContract("read-only routes must use GET")
            if self.service_role is not ControlPlaneServiceRole.READS:
                raise InvalidHttpApiContract("read-only routes must use reads service")
            if self.auth_scope is not HttpAuthScope.READ:
                raise InvalidHttpApiContract("read-only routes require read scope")
        elif self.safety is HttpOperationSafety.COMMAND:
            if self.method is HttpMethod.GET:
                raise InvalidHttpApiContract("command routes must not use GET")
            if self.auth_scope is HttpAuthScope.READ:
                raise InvalidHttpApiContract("command routes require command scope")
            if self.service_role is ControlPlaneServiceRole.READS:
                raise InvalidHttpApiContract("command routes must not use reads service")
        elif self.safety is HttpOperationSafety.DESTRUCTIVE:
            if self.method is HttpMethod.GET:
                raise InvalidHttpApiContract("destructive routes must not use GET")
            if self.auth_scope not in {
                HttpAuthScope.EXECUTION_RUN,
                HttpAuthScope.ADMIN,
            }:
                raise InvalidHttpApiContract(
                    "destructive routes require execution or admin scope"
                )

    def descriptor(self) -> dict[str, object]:
        return {
            "route_id": self.route_id,
            "method": self.method.value,
            "path_template": self.path_template,
            "service_role": self.service_role.value,
            "auth_scope": self.auth_scope.value,
            "safety": self.safety.value,
            "request_schema": self.request_schema.descriptor(),
            "response_schema": self.response_schema.descriptor(),
            "errors": self.errors.descriptor(),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "HttpApiRouteContract":
        if set(value) != {
            "route_id",
            "method",
            "path_template",
            "service_role",
            "auth_scope",
            "safety",
            "request_schema",
            "response_schema",
            "errors",
        }:
            raise InvalidHttpApiContract("route descriptor has unexpected keys")
        try:
            request_schema = value["request_schema"]
            response_schema = value["response_schema"]
            errors = value["errors"]
            if not isinstance(request_schema, Mapping):
                raise InvalidHttpApiContract("request_schema must be a descriptor")
            if not isinstance(response_schema, Mapping):
                raise InvalidHttpApiContract("response_schema must be a descriptor")
            if not isinstance(errors, Mapping):
                raise InvalidHttpApiContract("errors must be a descriptor")
            return cls(
                route_id=_text(value["route_id"], "route_id"),
                method=HttpMethod(_text(value["method"], "method")),
                path_template=_text(value["path_template"], "path_template"),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                auth_scope=HttpAuthScope(_text(value["auth_scope"], "auth_scope")),
                safety=HttpOperationSafety(_text(value["safety"], "safety")),
                request_schema=HttpSchemaRef.from_descriptor(request_schema),
                response_schema=HttpSchemaRef.from_descriptor(response_schema),
                errors=HttpErrorContract.from_descriptor(errors),
            )
        except ValueError as error:
            raise InvalidHttpApiContract(str(error)) from error


@dataclass(frozen=True)
class HttpApiContract:
    """Pure HTTP API contract for a set of operator routes."""

    routes: tuple[HttpApiRouteContract, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.routes, tuple) or not all(
            isinstance(route, HttpApiRouteContract) for route in self.routes
        ):
            raise InvalidHttpApiContract("routes must be HttpApiRouteContract values")
        by_route_id = {route.route_id: route for route in self.routes}
        if len(by_route_id) != len(self.routes):
            raise InvalidHttpApiContract("route_id values must be unique")
        by_method_path = {
            (route.method, route.path_template): route for route in self.routes
        }
        if len(by_method_path) != len(self.routes):
            raise InvalidHttpApiContract("method and path_template pairs must be unique")
        ordered = tuple(
            sorted(
                self.routes,
                key=lambda route: (
                    route.path_template,
                    route.method.value,
                    route.route_id,
                ),
            )
        )
        object.__setattr__(self, "routes", ordered)

    def route(self, route_id: str) -> HttpApiRouteContract:
        for route in self.routes:
            if route.route_id == route_id:
                return route
        raise InvalidHttpApiContract(f"unknown route_id {route_id!r}")

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "http-api-contract",
            "routes": [route.descriptor() for route in self.routes],
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "HttpApiContract":
        if set(value) != {"kind", "routes"}:
            raise InvalidHttpApiContract("HTTP API descriptor has unexpected keys")
        if value["kind"] != "http-api-contract":
            raise InvalidHttpApiContract("HTTP API descriptor has wrong kind")
        routes = value["routes"]
        if not isinstance(routes, list):
            raise InvalidHttpApiContract("routes must be a list")
        route_contracts = []
        for route in routes:
            if not isinstance(route, Mapping):
                raise InvalidHttpApiContract("routes must contain descriptors")
            route_contracts.append(HttpApiRouteContract.from_descriptor(route))
        return cls(tuple(route_contracts))


def operator_read_http_routes() -> tuple[HttpApiRouteContract, ...]:
    """Return the frozen read-route contract without importing a web framework."""

    return tuple(
        _read_route(route_id, path, response_schema)
        for route_id, path, response_schema in (
            (
                "read.workspace",
                "/workspaces/{workspace_id}",
                "WorkspaceReadResponse",
            ),
            (
                "read.current-graph",
                "/workspaces/{workspace_id}/graphs/current",
                "GraphReadResponse",
            ),
            (
                "read.desired-graph",
                "/workspaces/{workspace_id}/graphs/desired",
                "GraphReadResponse",
            ),
            (
                "read.operator-graph",
                "/workspaces/{workspace_id}/operator-graph",
                "OperatorGraphReadResponse",
            ),
            (
                "read.activity",
                "/workspaces/{workspace_id}/activity",
                "ActivityTimelineReadResponse",
            ),
            (
                "read.sessions",
                "/workspaces/{workspace_id}/sessions",
                "OpenSessionsReadResponse",
            ),
            (
                "read.session-detail",
                "/workspaces/{workspace_id}/sessions/{session_id}",
                "SessionDetailReadResponse",
            ),
            (
                "read.plan-detail",
                "/workspaces/{workspace_id}/plans/{plan_id}",
                "PlanDetailReadResponse",
            ),
            (
                "read.approval-detail",
                "/workspaces/{workspace_id}/approvals/{approval_id}",
                "ApprovalDetailReadResponse",
            ),
            (
                "read.pending-approvals",
                "/workspaces/{workspace_id}/approvals/pending",
                "PendingApprovalsReadResponse",
            ),
            (
                "read.observed-state",
                "/workspaces/{workspace_id}/observed-state",
                "ObservedStateReadResponse",
            ),
            (
                "read.control-surface",
                "/workspaces/{workspace_id}/control-surface",
                "ControlSurfaceReadResponse",
            ),
        )
    )


def operator_command_http_routes() -> tuple[HttpApiRouteContract, ...]:
    """Return the operator command-route contract without hosting an API."""

    return tuple(
        _command_route(
            route_id=route_id,
            path_template=path,
            service_role=service_role,
            auth_scope=auth_scope,
            safety=safety,
            request_schema=request_schema,
            response_schema=response_schema,
        )
        for (
            route_id,
            path,
            service_role,
            auth_scope,
            safety,
            request_schema,
            response_schema,
        ) in (
            (
                "command.workspace.create",
                "/workspaces",
                ControlPlaneServiceRole.PLANNING,
                HttpAuthScope.ADMIN,
                HttpOperationSafety.COMMAND,
                "CreateWorkspaceRequest",
                "WorkspaceReadResponse",
            ),
            (
                "command.product.import",
                "/workspaces/{workspace_id}/products/import",
                ControlPlaneServiceRole.PLANNING,
                HttpAuthScope.ADMIN,
                HttpOperationSafety.COMMAND,
                "ImportProductDescriptorRequest",
                "RegisteredProductResponse",
            ),
            (
                "command.operation-session.start",
                "/workspaces/{workspace_id}/sessions",
                ControlPlaneServiceRole.LIFECYCLE,
                HttpAuthScope.PLAN_WRITE,
                HttpOperationSafety.COMMAND,
                "StartOperationSessionRequest",
                "OperationCommandResult",
            ),
            (
                "command.operation-session.close",
                "/workspaces/{workspace_id}/sessions/{session_id}/close",
                ControlPlaneServiceRole.LIFECYCLE,
                HttpAuthScope.PLAN_WRITE,
                HttpOperationSafety.COMMAND,
                "CloseOperationSessionRequest",
                "OperationCommandResult",
            ),
            (
                "command.operation-session.cancel",
                "/workspaces/{workspace_id}/sessions/{session_id}/cancel",
                ControlPlaneServiceRole.LIFECYCLE,
                HttpAuthScope.PLAN_WRITE,
                HttpOperationSafety.COMMAND,
                "CancelOperationSessionRequest",
                "OperationCommandResult",
            ),
            (
                "command.operation-session.record-action",
                "/workspaces/{workspace_id}/sessions/{session_id}/actions",
                ControlPlaneServiceRole.LIFECYCLE,
                HttpAuthScope.PLAN_WRITE,
                HttpOperationSafety.COMMAND,
                "RecordOperationActionRequest",
                "OperationCommandResult",
            ),
            (
                "command.desired-graph.set",
                "/workspaces/{workspace_id}/graphs/desired",
                ControlPlaneServiceRole.PLANNING,
                HttpAuthScope.PLAN_WRITE,
                HttpOperationSafety.COMMAND,
                "SetDesiredGraphRequest",
                "DesiredGraphEditResult",
            ),
            (
                "command.deployment.plan",
                "/workspaces/{workspace_id}/plans",
                ControlPlaneServiceRole.PLANNING,
                HttpAuthScope.PLAN_WRITE,
                HttpOperationSafety.COMMAND,
                "PlanDeploymentRequest",
                "PlanDeploymentResponse",
            ),
            (
                "command.approval.request",
                "/workspaces/{workspace_id}/plans/{plan_id}/approval",
                ControlPlaneServiceRole.APPROVAL,
                HttpAuthScope.PLAN_WRITE,
                HttpOperationSafety.COMMAND,
                "ApprovalRequestRequest",
                "ApprovalRequestResponse",
            ),
            (
                "command.approval.decide",
                "/workspaces/{workspace_id}/approvals/{approval_id}/decision",
                ControlPlaneServiceRole.APPROVAL,
                HttpAuthScope.APPROVAL_DECIDE,
                HttpOperationSafety.COMMAND,
                "ApprovalDecisionRequest",
                "ApprovalDecisionResponse",
            ),
            (
                "command.deployment.admit",
                "/workspaces/{workspace_id}/plans/{plan_id}/admission",
                ControlPlaneServiceRole.ADMISSION,
                HttpAuthScope.EXECUTION_RUN,
                HttpOperationSafety.COMMAND,
                "AdmitDeploymentRequest",
                "AdmittedRunResponse",
            ),
            (
                "command.run.claim",
                "/workspaces/{workspace_id}/runs/{run_id}/claim",
                ControlPlaneServiceRole.LIFECYCLE,
                HttpAuthScope.EXECUTION_RUN,
                HttpOperationSafety.COMMAND,
                "ClaimRunRequest",
                "ClaimRunResponse",
            ),
            (
                "command.run.start",
                "/workspaces/{workspace_id}/runs/{run_id}/start",
                ControlPlaneServiceRole.EXECUTION,
                HttpAuthScope.EXECUTION_RUN,
                HttpOperationSafety.COMMAND,
                "StartRunRequest",
                "ActivityRunTransitionResult",
            ),
            (
                "command.deployment.execute",
                "/workspaces/{workspace_id}/runs/{run_id}/execute",
                ControlPlaneServiceRole.EXECUTION,
                HttpAuthScope.EXECUTION_RUN,
                HttpOperationSafety.DESTRUCTIVE,
                "ExecuteDeploymentRequest",
                "ExecutionRunResponse",
            ),
            (
                "command.graph.advance-current",
                "/workspaces/{workspace_id}/runs/{run_id}/advance-current-graph",
                ControlPlaneServiceRole.LIFECYCLE,
                HttpAuthScope.EXECUTION_RUN,
                HttpOperationSafety.COMMAND,
                "AdvanceCurrentGraphRequest",
                "CurrentGraphAdvancementResult",
            ),
            (
                "command.recovery.decide",
                "/workspaces/{workspace_id}/runs/{run_id}/recovery",
                ControlPlaneServiceRole.RECOVERY,
                HttpAuthScope.EXECUTION_RUN,
                HttpOperationSafety.COMMAND,
                "RecoveryDecisionRequest",
                "RecoveryDecisionResponse",
            ),
        )
    )


def _read_route(
    route_id: str,
    path_template: str,
    response_schema: str,
) -> HttpApiRouteContract:
    return HttpApiRouteContract(
        route_id=route_id,
        method=HttpMethod.GET,
        path_template=path_template,
        service_role=ControlPlaneServiceRole.READS,
        auth_scope=HttpAuthScope.READ,
        safety=HttpOperationSafety.READ_ONLY,
        response_schema=HttpSchemaRef(response_schema),
    )


def _command_route(
    *,
    route_id: str,
    path_template: str,
    service_role: ControlPlaneServiceRole,
    auth_scope: HttpAuthScope,
    safety: HttpOperationSafety,
    request_schema: str,
    response_schema: str,
) -> HttpApiRouteContract:
    return HttpApiRouteContract(
        route_id=route_id,
        method=HttpMethod.POST,
        path_template=path_template,
        service_role=service_role,
        auth_scope=auth_scope,
        safety=safety,
        request_schema=HttpSchemaRef(request_schema),
        response_schema=HttpSchemaRef(response_schema),
    )


def _validate_identity(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidHttpApiContract(f"{field} must be non-empty text")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(character not in allowed for character in value):
        raise InvalidHttpApiContract(
            f"{field} must contain only letters, numbers, dots, dashes, or underscores"
        )


def _validate_path_template(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("/") or value == "/":
        raise InvalidHttpApiContract(
            "path_template must be an absolute non-root path"
        )
    if "?" in value or "#" in value or any(character.isspace() for character in value):
        raise InvalidHttpApiContract(
            "path_template must not include query, fragment, or whitespace"
        )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidHttpApiContract(f"{field} must be text")
    return value
