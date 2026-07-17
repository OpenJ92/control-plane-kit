"""Typed SSRF and credential boundary for package control routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlsplit

from control_plane_kit.effects import EndpointMaterial, LiteralEndpointMaterial
from control_plane_kit.types import EndpointScope, Protocol as NetworkProtocol


class ControlAddressSource(StrEnum):
    """Trusted provenance categories for observed control endpoints."""

    DOCKER_PRIVATE = "docker-private"
    HOST_LOCAL = "host-local"
    EXPLICIT_PUBLIC = "explicit-public"


class ControlSecurityCode(StrEnum):
    """Closed failures that disclose neither authority nor credentials."""

    INVALID_OBSERVATION = "invalid-observation"
    UNSUPPORTED_SCHEME = "unsupported-scheme"
    UNTRUSTED_ADDRESS = "untrusted-address"
    UNSAFE_URL = "unsafe-url"
    MISSING_CREDENTIAL = "missing-credential"
    UNRESOLVED_CREDENTIAL = "unresolved-credential"


class ControlSecurityError(ValueError):
    """Fail-closed control-address error with deliberately bounded text."""

    def __init__(self, code: ControlSecurityCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RuntimeEndpointProvenance:
    """Evidence that an endpoint was observed under a particular runtime scope."""

    source: ControlAddressSource
    runtime_id: str
    network_id: str | None = None

    def __post_init__(self) -> None:
        if not self.runtime_id.strip():
            raise ControlSecurityError(
                ControlSecurityCode.INVALID_OBSERVATION,
                "runtime endpoint provenance requires an identity",
            )


@dataclass(frozen=True)
class ControlEndpointObservation:
    """One runtime-observed HTTP provider proposed as a control authority."""

    subject_id: str
    endpoint: EndpointMaterial
    provenance: RuntimeEndpointProvenance

    def __post_init__(self) -> None:
        if not self.subject_id.strip():
            raise ControlSecurityError(
                ControlSecurityCode.INVALID_OBSERVATION,
                "control endpoint observation requires a subject identity",
            )
        if not isinstance(self.endpoint, EndpointMaterial):
            raise TypeError("control endpoint observation requires EndpointMaterial")
        if not isinstance(self.provenance, RuntimeEndpointProvenance):
            raise TypeError("control endpoint observation requires runtime provenance")


@dataclass(frozen=True)
class CredentialReference:
    """Opaque identity resolved only while constructing transport headers."""

    reference_id: str

    def __post_init__(self) -> None:
        if not self.reference_id.strip() or any(value.isspace() for value in self.reference_id):
            raise ControlSecurityError(
                ControlSecurityCode.MISSING_CREDENTIAL,
                "control credential reference is missing or malformed",
            )


@dataclass(frozen=True, repr=False)
class SecretValue:
    """Ephemeral secret transport value that never has a revealing representation."""

    _value: str

    def __post_init__(self) -> None:
        if not self._value:
            raise ControlSecurityError(
                ControlSecurityCode.UNRESOLVED_CREDENTIAL,
                "control credential could not be resolved",
            )

    def bearer_header(self) -> str:
        return f"Bearer {self._value}"

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


class SecretResolver(Protocol):
    """Transport-owned secret lookup; implementations must not persist values."""

    def resolve(self, reference: CredentialReference) -> SecretValue: ...


class PublicAddressResolver(Protocol):
    """Resolve a public hostname to addresses pinned for one HTTP request."""

    def resolve(self, hostname: str) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class ControlTransportTarget:
    """One authorized connect authority with optional HTTP host and TLS SNI."""

    base_url: str
    host_header: str | None = None
    sni_hostname: str | None = None


@dataclass(frozen=True)
class ControlAddressPolicy:
    """Explicit trust roots for control endpoint authorization.

    Public hostname authorization is necessary but not sufficient for SSRF
    defense.  The transport must also resolve and pin an allowed destination so
    DNS rebinding cannot move an authorized hostname into a private address.
    """

    docker_networks: frozenset[str] = frozenset()
    public_hosts: frozenset[str] = frozenset()
    allow_host_local: bool = False
    allow_plaintext_public_http: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.docker_networks, frozenset) or not all(
            isinstance(value, str) and value.strip() for value in self.docker_networks
        ):
            raise TypeError("docker network policy must be a frozenset of identities")
        if not isinstance(self.public_hosts, frozenset) or not all(
            isinstance(value, str) and value.strip() for value in self.public_hosts
        ):
            raise TypeError("public host policy must be a frozenset of hostnames")


@dataclass(frozen=True, repr=False)
class AuthorizedControlEndpoint:
    """Transport authority plus ephemeral credential, safe only for bounded clients."""

    subject_id: str
    source: ControlAddressSource
    _base_url: str
    _credential: SecretValue

    def request_url(self, path: str) -> str:
        parsed_path = urlsplit(path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or parsed_path.scheme
            or parsed_path.netloc
            or parsed_path.query
            or parsed_path.fragment
        ):
            raise ControlSecurityError(
                ControlSecurityCode.UNSAFE_URL,
                "control route must be an absolute path on the authorized authority",
            )
        return f"{self._base_url}{path}"

    def request_headers(self, *, request_id: str, idempotency_key: str) -> dict[str, str]:
        if not _safe_header_value(request_id) or not _safe_header_value(idempotency_key):
            raise ControlSecurityError(
                ControlSecurityCode.UNSAFE_URL,
                "control request identity is required",
            )
        return {
            "Authorization": self._credential.bearer_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Control-Plane-Request-ID": request_id,
            "Idempotency-Key": idempotency_key,
        }

    def descriptor(self) -> dict[str, str]:
        return {
            "subject_id": self.subject_id,
            "source": self.source.value,
            "authority": "<redacted>",
            "credential": "<redacted>",
        }

    def transport_target(
        self,
        public_resolver: PublicAddressResolver | None = None,
    ) -> ControlTransportTarget:
        """Return a same-request transport target, pinning public DNS safely."""

        if self.source is not ControlAddressSource.EXPLICIT_PUBLIC:
            return ControlTransportTarget(self._base_url)
        if public_resolver is None:
            raise _untrusted()
        parsed = urlsplit(self._base_url)
        hostname = parsed.hostname or ""
        try:
            addresses = public_resolver.resolve(hostname)
        except Exception as error:
            raise _untrusted() from error
        if not addresses:
            raise _untrusted()
        parsed_addresses = []
        for value in addresses:
            try:
                candidate = ip_address(value)
            except ValueError as error:
                raise _untrusted() from error
            if not candidate.is_global:
                raise _untrusted()
            parsed_addresses.append(candidate)
        selected = sorted(parsed_addresses, key=lambda value: (value.version, int(value)))[0]
        rendered = f"[{selected}]" if selected.version == 6 else str(selected)
        port = parsed.port
        authority = f"{parsed.scheme}://{rendered}"
        if port is not None:
            authority += f":{port}"
        host_header = hostname if port is None else f"{hostname}:{port}"
        return ControlTransportTarget(authority, host_header, hostname)

    def __repr__(self) -> str:
        return (
            "AuthorizedControlEndpoint("
            f"subject_id={self.subject_id!r}, source={self.source!r}, "
            "authority=<redacted>, credential=<redacted>)"
        )


def authorize_control_endpoint(
    observation: ControlEndpointObservation,
    policy: ControlAddressPolicy,
    credential_reference: CredentialReference,
    resolver: SecretResolver,
) -> AuthorizedControlEndpoint:
    """Authorize one observed authority and resolve its credential at dispatch time."""

    if not isinstance(observation.endpoint.address, LiteralEndpointMaterial):
        raise ControlSecurityError(
            ControlSecurityCode.INVALID_OBSERVATION,
            "control authority must be a runtime-observed literal endpoint",
        )
    if observation.endpoint.protocol is not NetworkProtocol.HTTP:
        raise ControlSecurityError(
            ControlSecurityCode.INVALID_OBSERVATION,
            "control authority must use HTTP protocol",
        )
    parsed = urlsplit(observation.endpoint.address.value)
    _validate_url_shape(parsed)
    _authorize_source(observation, policy, parsed.scheme, parsed.hostname or "")
    try:
        credential = resolver.resolve(credential_reference)
    except ControlSecurityError:
        raise
    except Exception as error:
        raise ControlSecurityError(
            ControlSecurityCode.UNRESOLVED_CREDENTIAL,
            "control credential resolution failed",
        ) from error
    if not isinstance(credential, SecretValue):
        raise ControlSecurityError(
            ControlSecurityCode.UNRESOLVED_CREDENTIAL,
            "control credential resolver returned an invalid value",
        )
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError as error:
        raise ControlSecurityError(
            ControlSecurityCode.UNSAFE_URL,
            "control authority contains an invalid port",
        ) from error
    rendered_host = f"[{host}]" if ":" in host else host
    authority = f"{parsed.scheme}://{rendered_host}"
    if port is not None:
        authority += f":{port}"
    return AuthorizedControlEndpoint(
        observation.subject_id,
        observation.provenance.source,
        authority,
        credential,
    )


def _validate_url_shape(parsed) -> None:
    if parsed.scheme not in ("http", "https"):
        raise ControlSecurityError(
            ControlSecurityCode.UNSUPPORTED_SCHEME,
            "control authority uses an unsupported scheme",
        )
    if parsed.username is not None or parsed.password is not None:
        raise ControlSecurityError(
            ControlSecurityCode.UNSAFE_URL,
            "control authority cannot contain user information",
        )
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ControlSecurityError(
            ControlSecurityCode.UNSAFE_URL,
            "control authority must be an origin without path, query, or fragment",
        )


def _authorize_source(
    observation: ControlEndpointObservation,
    policy: ControlAddressPolicy,
    scheme: str,
    host: str,
) -> None:
    provenance = observation.provenance
    match provenance.source:
        case ControlAddressSource.DOCKER_PRIVATE:
            if (
                observation.endpoint.scope is not EndpointScope.PRIVATE
                or provenance.network_id not in policy.docker_networks
                or _is_loopback(host)
            ):
                raise _untrusted()
        case ControlAddressSource.HOST_LOCAL:
            if (
                observation.endpoint.scope is not EndpointScope.LOCAL
                or not policy.allow_host_local
                or not _is_loopback(host)
            ):
                raise _untrusted()
        case ControlAddressSource.EXPLICIT_PUBLIC:
            if (
                observation.endpoint.scope is not EndpointScope.PUBLIC
                or host not in policy.public_hosts
                or (scheme != "https" and not policy.allow_plaintext_public_http)
            ):
                raise _untrusted()


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _untrusted() -> ControlSecurityError:
    return ControlSecurityError(
        ControlSecurityCode.UNTRUSTED_ADDRESS,
        "control authority is outside the declared trust scope",
    )


def _safe_header_value(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 256
        and all(32 <= ord(character) < 127 for character in value)
    )
