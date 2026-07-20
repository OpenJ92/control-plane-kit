"""Typed authentication gateway contract and test-only API-key interpreter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import hmac
from typing import Protocol as TypingProtocol

from control_plane_kit.core.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.core.types import Protocol


class AuthenticationMechanism(StrEnum):
    API_KEY = "api-key"
    OIDC_JWT = "oidc-jwt"
    MTLS = "mtls"


class JwtAlgorithm(StrEnum):
    RS256 = "RS256"
    ES256 = "ES256"
    EDDSA = "EdDSA"


class GatewayMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ForwardedIdentityHeader(StrEnum):
    SUBJECT = "x-cpk-authenticated-subject"
    ISSUER = "x-cpk-authenticated-issuer"
    SCOPES = "x-cpk-authenticated-scopes"


class AuthenticationRejection(StrEnum):
    MISSING_CREDENTIAL = "missing-credential"
    INVALID_CREDENTIAL = "invalid-credential"
    IDENTITY_PROVIDER_UNAVAILABLE = "identity-provider-unavailable"


class AuthorizationDecision(StrEnum):
    ALLOWED = "allowed"
    ROUTE_NOT_ALLOWED = "route-not-allowed"
    MISSING_SCOPE = "missing-scope"


@dataclass(frozen=True)
class RouteAuthorizationPolicy:
    path_prefix: str
    methods: tuple[GatewayMethod, ...]
    required_scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.path_prefix.startswith("/") or "?" in self.path_prefix:
            raise ValueError("gateway route prefix must be an absolute path")
        if not self.methods or any(not isinstance(value, GatewayMethod) for value in self.methods):
            raise TypeError("gateway methods must be a nonempty typed tuple")
        if len(set(self.methods)) != len(self.methods):
            raise ValueError("gateway methods must be unique")
        _bounded_names("gateway required scopes", self.required_scopes)


@dataclass(frozen=True)
class AuthGatewayPolicy:
    mechanism: AuthenticationMechanism
    routes: tuple[RouteAuthorizationPolicy, ...]
    accepted_issuers: tuple[str, ...] = ()
    accepted_audiences: tuple[str, ...] = ()
    accepted_algorithms: tuple[JwtAlgorithm, ...] = ()
    forwarded_headers: tuple[ForwardedIdentityHeader, ...] = (
        ForwardedIdentityHeader.SUBJECT,
        ForwardedIdentityHeader.SCOPES,
    )
    max_request_bytes: int = 65_536
    max_response_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if not isinstance(self.mechanism, AuthenticationMechanism):
            raise TypeError("gateway authentication mechanism must be typed")
        if not self.routes or any(not isinstance(value, RouteAuthorizationPolicy) for value in self.routes):
            raise TypeError("gateway routes must be a nonempty typed tuple")
        if len({(route.path_prefix, route.methods) for route in self.routes}) != len(self.routes):
            raise ValueError("gateway route policies must be unique")
        _bounded_names("gateway accepted issuers", self.accepted_issuers)
        _bounded_names("gateway accepted audiences", self.accepted_audiences)
        if any(not isinstance(value, JwtAlgorithm) for value in self.accepted_algorithms):
            raise TypeError("gateway algorithms must be typed")
        if len(set(self.accepted_algorithms)) != len(self.accepted_algorithms):
            raise ValueError("gateway algorithms must be unique")
        if any(not isinstance(value, ForwardedIdentityHeader) for value in self.forwarded_headers):
            raise TypeError("gateway forwarded headers must be typed")
        if len(set(self.forwarded_headers)) != len(self.forwarded_headers):
            raise ValueError("gateway forwarded headers must be unique")
        _bounded("gateway request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("gateway response byte limit", self.max_response_bytes, 1, 1_048_576)
        match self.mechanism:
            case AuthenticationMechanism.OIDC_JWT:
                if not self.accepted_issuers or not self.accepted_audiences or not self.accepted_algorithms:
                    raise ValueError("OIDC JWT policy requires issuer, audience, and algorithm allowlists")
            case AuthenticationMechanism.API_KEY | AuthenticationMechanism.MTLS:
                if self.accepted_issuers or self.accepted_audiences or self.accepted_algorithms:
                    raise ValueError("issuer, audience, and algorithm policy belongs only to OIDC JWT")

    def descriptor(self) -> dict[str, object]:
        return {
            "mechanism": self.mechanism.value,
            "routes": [
                {
                    "path_prefix": route.path_prefix,
                    "methods": [value.value for value in route.methods],
                    "required_scopes": list(route.required_scopes),
                }
                for route in self.routes
            ],
            "accepted_issuers": list(self.accepted_issuers),
            "accepted_audiences": list(self.accepted_audiences),
            "accepted_algorithms": [value.value for value in self.accepted_algorithms],
            "forwarded_headers": [value.value for value in self.forwarded_headers],
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
        }


def auth_gateway_policy_from_descriptor(value: Mapping[str, object]) -> AuthGatewayPolicy:
    expected = {
        "mechanism",
        "routes",
        "accepted_issuers",
        "accepted_audiences",
        "accepted_algorithms",
        "forwarded_headers",
        "max_request_bytes",
        "max_response_bytes",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("gateway policy descriptor has unknown or missing fields")
    routes_value = value["routes"]
    if not isinstance(routes_value, list):
        raise TypeError("gateway routes descriptor must be a list")
    routes: list[RouteAuthorizationPolicy] = []
    for route_value in routes_value:
        if not isinstance(route_value, Mapping) or set(route_value) != {
            "path_prefix",
            "methods",
            "required_scopes",
        }:
            raise ValueError("gateway route descriptor has unknown or missing fields")
        routes.append(
            RouteAuthorizationPolicy(
                _string(route_value["path_prefix"], "gateway route prefix"),
                tuple(GatewayMethod(item) for item in _string_list(route_value["methods"], "gateway methods")),
                tuple(_string_list(route_value["required_scopes"], "gateway required scopes")),
            )
        )
    return AuthGatewayPolicy(
        AuthenticationMechanism(_string(value["mechanism"], "gateway mechanism")),
        tuple(routes),
        tuple(_string_list(value["accepted_issuers"], "gateway accepted issuers")),
        tuple(_string_list(value["accepted_audiences"], "gateway accepted audiences")),
        tuple(JwtAlgorithm(item) for item in _string_list(value["accepted_algorithms"], "gateway algorithms")),
        tuple(ForwardedIdentityHeader(item) for item in _string_list(value["forwarded_headers"], "gateway forwarded headers")),
        _integer(value["max_request_bytes"], "gateway request byte limit"),
        _integer(value["max_response_bytes"], "gateway response byte limit"),
    )


@dataclass(frozen=True)
class AuthenticatedIdentity:
    subject: str
    issuer: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        _bounded_name("authenticated subject", self.subject)
        _bounded_name("authenticated issuer", self.issuer)
        _bounded_names("authenticated scopes", self.scopes)


@dataclass(frozen=True)
class AuthenticationAccepted:
    identity: AuthenticatedIdentity


@dataclass(frozen=True)
class AuthenticationRejected:
    reason: AuthenticationRejection


AuthenticationResult = AuthenticationAccepted | AuthenticationRejected


class IdentityValidator(TypingProtocol):
    """Adapter boundary implemented by API-key, JWT/OIDC, or mTLS products."""

    def authenticate(self, request: HttpRequest) -> AuthenticationResult: ...


@dataclass(frozen=True)
class StaticApiKeyValidator:
    """Test-only API-key adapter; production integrations belong outside CPK."""

    credential: str = field(repr=False)
    identity: AuthenticatedIdentity
    header_name: str = "x-api-key"

    def __post_init__(self) -> None:
        if not self.credential or len(self.credential) > 4_096:
            raise ValueError("API-key credential must be nonempty and bounded")
        if self.header_name.lower() != "x-api-key":
            raise ValueError("test API-key adapter uses the closed x-api-key header")

    def authenticate(self, request: HttpRequest) -> AuthenticationResult:
        supplied = _header(request.headers, self.header_name)
        if not supplied:
            return AuthenticationRejected(AuthenticationRejection.MISSING_CREDENTIAL)
        if not hmac.compare_digest(supplied, self.credential):
            return AuthenticationRejected(AuthenticationRejection.INVALID_CREDENTIAL)
        return AuthenticationAccepted(self.identity)


@dataclass(frozen=True)
class AuthGatewayObservation:
    request_count: int
    authenticated_count: int
    rejected_count: int
    forbidden_count: int
    forwarded_count: int
    latest_authentication: AuthenticationRejection | None
    latest_authorization: AuthorizationDecision | None

    def descriptor(self) -> dict[str, object]:
        return {
            "request_count": self.request_count,
            "authenticated_count": self.authenticated_count,
            "rejected_count": self.rejected_count,
            "forbidden_count": self.forbidden_count,
            "forwarded_count": self.forwarded_count,
            "latest_authentication": None if self.latest_authentication is None else self.latest_authentication.value,
            "latest_authorization": None if self.latest_authorization is None else self.latest_authorization.value,
        }


@dataclass
class HttpAuthGatewayServer:
    targets: Mapping[str, HttpHandler]
    target: str
    policy: AuthGatewayPolicy
    validator: IdentityValidator
    _request_count: int = field(init=False, default=0)
    _authenticated_count: int = field(init=False, default=0)
    _rejected_count: int = field(init=False, default=0)
    _forbidden_count: int = field(init=False, default=0)
    _forwarded_count: int = field(init=False, default=0)
    _latest_authentication: AuthenticationRejection | None = field(init=False, default=None)
    _latest_authorization: AuthorizationDecision | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")

    def handle(self, request: HttpRequest) -> HttpResponse:
        self._request_count += 1
        if len(request.body) > self.policy.max_request_bytes:
            self._rejected_count += 1
            return HttpResponse.text("Request Entity Too Large", status_code=413)
        sanitized = _strip_identity_headers(request)
        match self.validator.authenticate(sanitized):
            case AuthenticationRejected(reason):
                self._rejected_count += 1
                self._latest_authentication = reason
                status = 503 if reason is AuthenticationRejection.IDENTITY_PROVIDER_UNAVAILABLE else 401
                return HttpResponse.text("Unauthorized" if status == 401 else "Service Unavailable", status_code=status)
            case AuthenticationAccepted(identity):
                self._authenticated_count += 1
                self._latest_authentication = None
        decision = authorize_request(self.policy, sanitized, identity)
        self._latest_authorization = decision
        if decision is not AuthorizationDecision.ALLOWED:
            self._forbidden_count += 1
            return HttpResponse.text("Forbidden", status_code=403)
        forwarded = _with_identity_headers(
            _strip_gateway_credentials(sanitized),
            identity,
            self.policy.forwarded_headers,
        )
        try:
            response = self.targets[self.target](forwarded)
        except Exception:  # noqa: BLE001 - downstream loss is not an auth decision.
            return HttpResponse.text("Bad Gateway", status_code=502)
        if len(response.body) > self.policy.max_response_bytes:
            return HttpResponse.text("Bad Gateway", status_code=502)
        self._forwarded_count += 1
        return response

    def observation(self) -> AuthGatewayObservation:
        return AuthGatewayObservation(
            self._request_count,
            self._authenticated_count,
            self._rejected_count,
            self._forbidden_count,
            self._forwarded_count,
            self._latest_authentication,
            self._latest_authorization,
        )


def authorize_request(policy: AuthGatewayPolicy, request: HttpRequest, identity: AuthenticatedIdentity) -> AuthorizationDecision:
    method = request.method.upper()
    matches = tuple(
        route for route in policy.routes
        if _path_matches_prefix(request.path, route.path_prefix)
        and any(value.value == method for value in route.methods)
    )
    if not matches:
        return AuthorizationDecision.ROUTE_NOT_ALLOWED
    route = max(matches, key=lambda value: len(value.path_prefix))
    if not set(route.required_scopes).issubset(identity.scopes):
        return AuthorizationDecision.MISSING_SCOPE
    return AuthorizationDecision.ALLOWED


def http_auth_gateway_block(
    block_id: str = "http-auth-gateway",
    *,
    display_name: str = "HTTP Authentication Gateway",
    image: str = "python:3.14-alpine",
    policy: AuthGatewayPolicy,
    api_key_scopes: tuple[str, ...] = (),
    credential_secret_reference: str = "secret://http-auth-gateway/api-key",
    control_secret_reference: str = "secret://http-auth-gateway/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_AUTH_GATEWAY,
            maturity=ProductMaturity.TEST_ONLY,
            display_name=display_name,
            health_path="/health",
            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.METRICS_READABLE),
        ),
        DockerImageImplementation(
            image=image,
            command=http_auth_gateway_command(policy, api_key_scopes=api_key_scopes),
            ports={"internal": 8080},
            secret_deliveries=(
                SecretEnvironmentDelivery("CPK_GATEWAY_API_KEY", SecretReference(credential_secret_reference)),
                SecretEnvironmentDelivery("CPK_GATEWAY_CONTROL_TOKEN", SecretReference(control_secret_reference)),
            ),
        ),
        BlockSockets(
            requirements=(RequirementSocket("target", Protocol.HTTP, ("AUTH_GATEWAY_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_auth_gateway_command(
    policy: AuthGatewayPolicy,
    *,
    api_key_scopes: tuple[str, ...] = (),
    port: int = 8080,
) -> tuple[str, ...]:
    if not isinstance(policy, AuthGatewayPolicy):
        raise TypeError("gateway policy must be typed")
    if policy.mechanism is not AuthenticationMechanism.API_KEY:
        raise ValueError("package test interpreter supports only API-key policy")
    _bounded_names("gateway API-key scopes", api_key_scopes)
    _bounded("gateway port", port, 1, 65_535)
    return render_python_command(
        "http_auth_gateway.py.j2",
        routes=[
            {"path_prefix": route.path_prefix, "methods": [value.value for value in route.methods], "required_scopes": list(route.required_scopes)}
            for route in policy.routes
        ],
        forwarded_headers=[value.value for value in policy.forwarded_headers],
        api_key_scopes=list(api_key_scopes),
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        port=port,
    )


def _strip_identity_headers(request: HttpRequest) -> HttpRequest:
    trusted = {value.value for value in ForwardedIdentityHeader}
    return HttpRequest(request.method, request.path, request.query, {key: value for key, value in request.headers.items() if key.lower() not in trusted}, request.body)


def _strip_gateway_credentials(request: HttpRequest) -> HttpRequest:
    credential_headers = {"authorization", "x-api-key"}
    return HttpRequest(
        request.method,
        request.path,
        request.query,
        {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in credential_headers
        },
        request.body,
    )


def _path_matches_prefix(path: str, prefix: str) -> bool:
    return prefix == "/" or path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _with_identity_headers(request: HttpRequest, identity: AuthenticatedIdentity, headers: tuple[ForwardedIdentityHeader, ...]) -> HttpRequest:
    values = dict(request.headers)
    for header in headers:
        match header:
            case ForwardedIdentityHeader.SUBJECT:
                values[header.value] = identity.subject
            case ForwardedIdentityHeader.ISSUER:
                values[header.value] = identity.issuer
            case ForwardedIdentityHeader.SCOPES:
                values[header.value] = " ".join(identity.scopes)
    return HttpRequest(request.method, request.path, request.query, values, request.body)


def _header(headers: Mapping[str, str], name: str) -> str:
    return next((value for key, value in headers.items() if key.lower() == name.lower()), "")


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


def _bounded_name(label: str, value: str) -> None:
    if not value or len(value.encode()) > 512 or any(character in value for character in "\r\n\0"):
        raise ValueError(f"{label} must be nonempty, bounded, and header-safe")


def _bounded_names(label: str, values: tuple[str, ...]) -> None:
    if len(values) > 128 or len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique and bounded")
    for value in values:
        _bounded_name(label, value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} descriptor must be a string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{label} descriptor must be a string list")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} descriptor must be an integer")
    return value
