"""Fail-closed outbound HTTP interpretation for webhook delivery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import hmac
from ipaddress import ip_address
import socket
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from control_plane_kit.core.secrets import SecretResolver, require_resolved_secret
from control_plane_kit.domains.webhook.language import (
    WebhookAttemptOutcome,
    WebhookEndpoint,
    WebhookSigningAlgorithm,
)
from control_plane_kit.operations.webhook import (
    WebhookOutboundRequest,
    WebhookOutboundResult,
)


class WebhookEndpointScope(StrEnum):
    PUBLIC = "public"
    HOST_LOCAL = "host-local"
    RUNTIME_PRIVATE = "runtime-private"


@dataclass(frozen=True, slots=True)
class WebhookEndpointGrant:
    """Exact endpoint authority granted by process bootstrap configuration."""

    endpoint_id: str
    url: str
    scope: WebhookEndpointScope

    def __post_init__(self) -> None:
        endpoint = WebhookEndpoint(self.endpoint_id, self.url)
        if endpoint.url != self.url:
            raise ValueError("webhook endpoint grant must be canonical")
        if not isinstance(self.scope, WebhookEndpointScope):
            raise TypeError("webhook endpoint grant scope must be typed")


@dataclass(frozen=True, slots=True)
class WebhookAddressPolicy:
    """Closed exact allowlist for outbound webhook destinations."""

    grants: tuple[WebhookEndpointGrant, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.grants, tuple) or any(
            not isinstance(grant, WebhookEndpointGrant) for grant in self.grants
        ):
            raise TypeError("webhook address policy grants must be typed")
        identities = tuple(grant.endpoint_id for grant in self.grants)
        if len(set(identities)) != len(identities):
            raise ValueError("webhook address policy endpoint identities must be unique")

    def grant_for(self, endpoint: WebhookEndpoint) -> WebhookEndpointGrant:
        matches = tuple(grant for grant in self.grants if grant.endpoint_id == endpoint.endpoint_id)
        if len(matches) != 1 or matches[0].url != endpoint.url:
            raise WebhookHttpSecurityError("webhook endpoint is not explicitly authorized")
        return matches[0]


class WebhookPublicAddressResolver(Protocol):
    def resolve(self, hostname: str) -> tuple[str, ...]: ...


class SystemWebhookPublicAddressResolver:
    """Resolve all public address candidates for same-request policy pinning."""

    def resolve(self, hostname: str) -> tuple[str, ...]:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        }
        return tuple(sorted(addresses))


class WebhookHttpSecurityError(ValueError):
    """Bounded destination rejection that never echoes the endpoint."""


@dataclass(frozen=True, slots=True)
class WebhookHttpLimits:
    timeout_seconds: int = 10
    response_bytes: int = 65_536
    response_header_bytes: int = 16_384
    response_headers: int = 128

    def __post_init__(self) -> None:
        for value in (
            self.timeout_seconds,
            self.response_bytes,
            self.response_header_bytes,
            self.response_headers,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("webhook HTTP limits must be positive integers")


@dataclass(frozen=True, slots=True)
class _AuthorizedWebhookTarget:
    request_url: str
    host_header: str | None = None
    sni_hostname: str | None = None


class HttpWebhookDelivery:
    """Interpret one webhook effect through bounded, redirect-free HTTP."""

    def __init__(
        self,
        secrets: SecretResolver,
        policy: WebhookAddressPolicy,
        *,
        public_resolver: WebhookPublicAddressResolver | None = None,
        limits: WebhookHttpLimits | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._secrets = secrets
        self._policy = policy
        self._public_resolver = public_resolver
        self._limits = limits or WebhookHttpLimits()
        self._transport = transport

    def deliver(self, request: WebhookOutboundRequest) -> WebhookOutboundResult:
        try:
            target = _authorize_target(request.endpoint, self._policy, self._public_resolver)
            headers = {
                "Content-Type": request.payload.content_type.value,
                "User-Agent": "control-plane-kit-webhook/1",
                "X-CPK-Webhook-Delivery": request.identity.delivery_id,
                "X-CPK-Webhook-Attempt": str(request.attempt_number),
            }
            if target.host_header is not None:
                headers["Host"] = target.host_header
            if request.signing is not None:
                secret = require_resolved_secret(
                    self._secrets,
                    request.signing.secret_reference,
                )
                match request.signing.algorithm:
                    case WebhookSigningAlgorithm.HMAC_SHA256:
                        digest = hmac.new(
                            secret.reveal().encode(),
                            request.payload.body,
                            hashlib.sha256,
                        ).hexdigest()
                headers[request.signing.header_name] = f"sha256={digest}"
        except Exception:
            return WebhookOutboundResult(
                WebhookAttemptOutcome.TERMINAL_FAILURE,
                failure_code="webhook.destination-or-signing-rejected",
            )

        timeout = httpx.Timeout(
            self._limits.timeout_seconds,
            connect=min(self._limits.timeout_seconds, 5),
            read=self._limits.timeout_seconds,
            write=self._limits.timeout_seconds,
            pool=min(self._limits.timeout_seconds, 5),
        )
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                outbound = client.build_request(
                    "POST",
                    target.request_url,
                    headers=headers,
                    content=request.payload.body,
                )
                if target.sni_hostname is not None:
                    outbound.extensions["sni_hostname"] = target.sni_hostname
                response = client.send(outbound, stream=True)
                try:
                    _validate_response_headers(response, self._limits)
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self._limits.response_bytes:
                            return _uncertain("http.response-too-large")
                finally:
                    response.close()
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return _retryable("http.connect-failure")
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
            return _uncertain("http.timeout")
        except httpx.HTTPError:
            return _uncertain("http.transport-uncertain")
        except WebhookHttpSecurityError:
            return _uncertain("http.response-headers-rejected")

        status = response.status_code
        if 200 <= status < 300:
            return WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, status)
        if 300 <= status < 400:
            return WebhookOutboundResult(
                WebhookAttemptOutcome.TERMINAL_FAILURE,
                status,
                "http.redirect-rejected",
            )
        if status in {408, 425, 429} or status >= 500:
            return WebhookOutboundResult(
                WebhookAttemptOutcome.RETRYABLE_FAILURE,
                status,
                "http.retryable-response",
            )
        return WebhookOutboundResult(
            WebhookAttemptOutcome.TERMINAL_FAILURE,
            status,
            "http.rejected",
        )


def _authorize_target(
    endpoint: WebhookEndpoint,
    policy: WebhookAddressPolicy,
    resolver: WebhookPublicAddressResolver | None,
) -> _AuthorizedWebhookTarget:
    grant = policy.grant_for(endpoint)
    parsed = urlsplit(endpoint.url)
    host = parsed.hostname or ""
    match grant.scope:
        case WebhookEndpointScope.HOST_LOCAL:
            if not _is_loopback(host):
                raise WebhookHttpSecurityError("host-local webhook endpoint is not loopback")
            return _AuthorizedWebhookTarget(endpoint.url)
        case WebhookEndpointScope.RUNTIME_PRIVATE:
            if (
                _is_loopback(host)
                or _is_metadata_or_link_local(host)
                or _is_global_ip(host)
                or _is_unsafe_non_global_ip(host)
            ):
                raise WebhookHttpSecurityError("runtime-private webhook endpoint is outside scope")
            return _AuthorizedWebhookTarget(endpoint.url)
        case WebhookEndpointScope.PUBLIC:
            if parsed.scheme != "https":
                raise WebhookHttpSecurityError("public webhook endpoint requires HTTPS")
            address = _public_address(host, resolver)
            rendered = f"[{address}]" if ":" in address else address
            port = parsed.port
            authority = rendered if port is None else f"{rendered}:{port}"
            request_url = urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
            host_header = host if port is None else f"{host}:{port}"
            return _AuthorizedWebhookTarget(request_url, host_header, host)
    raise WebhookHttpSecurityError("webhook endpoint scope is unsupported")


def _public_address(host: str, resolver: WebhookPublicAddressResolver | None) -> str:
    if _is_loopback(host) or _is_metadata_or_link_local(host):
        raise WebhookHttpSecurityError("public webhook endpoint is untrusted")
    try:
        literal = ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise WebhookHttpSecurityError("public webhook endpoint is not global")
        return str(literal)
    if resolver is None:
        raise WebhookHttpSecurityError("public webhook endpoint requires DNS pinning")
    try:
        addresses = tuple(ip_address(value) for value in resolver.resolve(host))
    except Exception as error:
        raise WebhookHttpSecurityError("public webhook endpoint resolution failed") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise WebhookHttpSecurityError("public webhook endpoint resolution is untrusted")
    return str(sorted(addresses, key=lambda value: (value.version, int(value)))[0])


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _is_global_ip(host: str) -> bool:
    try:
        return ip_address(host).is_global
    except ValueError:
        return False


def _is_unsafe_non_global_ip(host: str) -> bool:
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return not address.is_private or any(
        (
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def _is_metadata_or_link_local(host: str) -> bool:
    try:
        address = ip_address(host)
    except ValueError:
        return host.lower() in {"metadata", "metadata.google.internal"}
    return address.is_link_local or str(address) == "169.254.169.254"


def _validate_response_headers(response: httpx.Response, limits: WebhookHttpLimits) -> None:
    if len(response.headers) > limits.response_headers:
        raise WebhookHttpSecurityError("webhook response has too many headers")
    size = sum(len(key.encode()) + len(value.encode()) for key, value in response.headers.items())
    if size > limits.response_header_bytes:
        raise WebhookHttpSecurityError("webhook response headers exceed bound")


def _retryable(code: str) -> WebhookOutboundResult:
    return WebhookOutboundResult(WebhookAttemptOutcome.RETRYABLE_FAILURE, failure_code=code)


def _uncertain(code: str) -> WebhookOutboundResult:
    return WebhookOutboundResult(WebhookAttemptOutcome.UNCERTAIN, failure_code=code)
