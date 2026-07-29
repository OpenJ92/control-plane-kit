"""HTTP-shaped and MCP-shaped process boundaries for cpk-server."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Protocol

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    CredentialVerifier,
)
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.operations.http import (
    HttpApiRouteContract,
    HttpMethod,
    HttpOperationSafety,
)
from control_plane_kit_operations import CpkServerApplicationError

from .composition import CpkServerComposition, CpkServerCompositionError
from .authentication import (
    CredentialAuthenticationError,
    authenticate_bearer_credential,
)


class CpkServerService(Protocol):
    def handle(self, request: "CpkServerServiceRequest") -> Mapping[str, object]:
        ...


@dataclass(frozen=True, slots=True)
class CpkServerServiceRequest:
    """One bounded request delegated to an application service."""

    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, str]
    payload: Mapping[str, object]
    principal: AuthenticatedPrincipal


@dataclass(frozen=True, slots=True)
class CpkServerBoundaryResponse:
    """Small framework-neutral response value."""

    status: int
    body: Mapping[str, object]


class CpkServerApplicationBoundary:
    """Shared service boundary used by HTTP and MCP process surfaces."""

    def __init__(
        self,
        services: Mapping[ControlPlaneServiceRole, CpkServerService],
        credential_verifier: CredentialVerifier,
    ) -> None:
        missing = tuple(role for role in ControlPlaneServiceRole if role not in services)
        if missing:
            missing_names = ", ".join(role.value for role in missing)
            raise CpkServerCompositionError(f"missing services: {missing_names}")
        if not callable(getattr(credential_verifier, "authenticate", None)):
            raise CpkServerCompositionError("credential verifier is required")
        self._services = dict(services)
        self._credential_verifier = credential_verifier

    def authenticate(
        self,
        headers: Mapping[str, str],
    ) -> AuthenticatedPrincipal:
        return authenticate_bearer_credential(headers, self._credential_verifier)

    def dispatch(self, request: CpkServerServiceRequest) -> Mapping[str, object]:
        return self._services[request.service_role].handle(request)


class CpkServerHttpProcessBoundary:
    """Framework-neutral HTTP process boundary over core route contracts."""

    def __init__(
        self,
        composition: CpkServerComposition,
        application: CpkServerApplicationBoundary,
    ) -> None:
        self.composition = composition
        self.application = application

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> CpkServerBoundaryResponse:
        route_match = _match_http_route(self.composition, method, path)
        if route_match is None:
            return _error(404, "unknown route")
        route, path_parameters = route_match
        try:
            principal = self.application.authenticate(headers)
        except CredentialAuthenticationError:
            return _error(401, "invalid credential")
        if len(body) > route.request_schema.max_bytes:
            return _error(413, "request body too large")
        payload = _decode_http_payload(route, body)
        if isinstance(payload, CpkServerBoundaryResponse):
            return payload
        request = CpkServerServiceRequest(
            surface="http",
            route_id=route.route_id,
            service_role=route.service_role,
            path_parameters=path_parameters,
            payload=payload,
            principal=principal,
        )
        response = _dispatch_application(self.application, request)
        if isinstance(response, CpkServerBoundaryResponse):
            return response
        result = response
        return CpkServerBoundaryResponse(200, dict(result))


class CpkServerMcpProcessBoundary:
    """Framework-neutral MCP Streamable HTTP boundary over one application."""

    def __init__(
        self,
        composition: CpkServerComposition,
        application: CpkServerApplicationBoundary,
    ) -> None:
        self.composition = composition
        self.application = application

    def handle(
        self,
        *,
        headers: Mapping[str, str],
        message: Mapping[str, object],
    ) -> CpkServerBoundaryResponse:
        header_error = _validate_mcp_headers(headers)
        if header_error is not None:
            return header_error
        try:
            principal = self.application.authenticate(headers)
        except CredentialAuthenticationError:
            return _error(401, "invalid credential")
        method_header = next(
            (value for key, value in headers.items() if key.lower() == "mcp-method"),
            None,
        )
        if method_header != message.get("method"):
            return _error(400, "MCP method header does not match message")
        request = _decode_mcp_message(self.composition, message, principal)
        if isinstance(request, CpkServerBoundaryResponse):
            return request
        response = _dispatch_application(self.application, request)
        if isinstance(response, CpkServerBoundaryResponse):
            return response
        result = response
        return CpkServerBoundaryResponse(
            200,
            {
                "jsonrpc": "2.0",
                "id": _message_id(message),
                "result": dict(result),
            },
        )


def _match_http_route(
    composition: CpkServerComposition,
    method: str,
    path: str,
) -> tuple[HttpApiRouteContract, dict[str, str]] | None:
    try:
        http_method = HttpMethod(method.upper())
    except ValueError:
        return None
    for route in composition.http_api.routes:
        if route.method is not http_method:
            continue
        parameters = _match_path_template(route.path_template, path)
        if parameters is not None:
            return route, parameters
    return None


def _match_path_template(template: str, path: str) -> dict[str, str] | None:
    names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template)
    pattern = "^" + re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", r"([^/]+)", template) + "$"
    match = re.match(pattern, path)
    if match is None:
        return None
    return dict(zip(names, match.groups(), strict=True))


def _decode_http_payload(
    route: HttpApiRouteContract,
    body: bytes,
) -> Mapping[str, object] | CpkServerBoundaryResponse:
    if route.method is HttpMethod.GET:
        if body not in {b"", None}:
            return _error(400, "read routes do not accept request bodies")
        return {}
    if body == b"":
        return {}
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(400, "invalid JSON request body")
    if not isinstance(decoded, dict):
        return _error(400, "request body must be an object")
    return decoded


def _validate_mcp_headers(headers: Mapping[str, str]) -> CpkServerBoundaryResponse | None:
    lowered = {key.lower(): value for key, value in headers.items()}
    if "accept" not in lowered:
        return _error(400, "missing MCP Accept header")
    if "mcp-protocol-version" not in lowered:
        return _error(400, "missing MCP protocol version")
    if "mcp-method" not in lowered:
        return _error(400, "missing MCP method")
    return None


def _decode_mcp_message(
    composition: CpkServerComposition,
    message: Mapping[str, object],
    principal: AuthenticatedPrincipal,
) -> CpkServerServiceRequest | CpkServerBoundaryResponse:
    if not isinstance(message, Mapping):
        return _error(400, "MCP message must be an object")
    if message.get("jsonrpc") != "2.0":
        return _error(400, "MCP message must be JSON-RPC 2.0")
    method = message.get("method")
    params = message.get("params")
    if method not in {"tools/call", "resources/read"}:
        return _error(404, "unknown MCP method")
    if not isinstance(params, Mapping):
        return _error(400, "MCP params must be an object")
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str):
        return _error(400, "MCP operation name must be text")
    if not isinstance(arguments, Mapping):
        return _error(400, "MCP arguments must be an object")
    try:
        route = composition.http_api.route(name)
    except ValueError:
        return _error(404, "unknown MCP operation")
    if method == "tools/call" and route.safety is HttpOperationSafety.READ_ONLY:
        return _error(400, "tools/call requires a command route")
    if method == "resources/read" and route.safety is not HttpOperationSafety.READ_ONLY:
        return _error(400, "resources/read requires a read route")
    return CpkServerServiceRequest(
        surface="mcp",
        route_id=route.route_id,
        service_role=route.service_role,
        path_parameters={},
        payload=dict(arguments),
        principal=principal,
    )


def _message_id(message: Mapping[str, object]) -> object:
    value = message.get("id")
    if isinstance(value, (str, int)):
        return value
    return None


def _error(status: int, message: str) -> CpkServerBoundaryResponse:
    return CpkServerBoundaryResponse(
        status,
        {
            "error": {
                "message": message,
                "status": status,
            }
        },
    )


def _dispatch_application(
    application: CpkServerApplicationBoundary,
    request: CpkServerServiceRequest,
) -> Mapping[str, object] | CpkServerBoundaryResponse:
    try:
        return application.dispatch(request)
    except CpkServerApplicationError as error:
        return CpkServerBoundaryResponse(error.status, error.descriptor())
    except Exception:  # noqa: BLE001 - process boundary must fail closed without leaking details.
        return _error(500, "application service failed")
