"""Runtime-only secret resolution with no durable secret-value language."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping, Protocol, TypeAlias
from urllib.parse import urlsplit

from control_plane_kit_core._activity_identity import (
    _is_canonical_activity_identity,
)


_PROVIDER_ID = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_REFERENCE_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_ENDPOINT_REFERENCE = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_ENDPOINT_REFERENCE_KEYS = frozenset({"reference_id"})
_GRANT_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SecretResolutionCode(StrEnum):
    MALFORMED_REFERENCE = "malformed-reference"
    MISSING = "missing"
    DENIED = "denied"
    INVALID_RESOLVER_RESULT = "invalid-resolver-result"


class SecretResolutionError(ValueError):
    """A bounded failure that never includes resolved secret content."""

    def __init__(self, code: SecretResolutionCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class SecretFileMode(StrEnum):
    OWNER_READ_ONLY = "0400"


class SecretProviderContractError(ValueError):
    """Raised when provider-neutral secret admission material is malformed."""


class SecretUseIntent(StrEnum):
    """Closed reason why one admitted secret may be resolved."""

    APPLICATION_CONTROL_TOKEN = "application.control-token"
    CLOUDFLARE_API_TOKEN = "cloudflare.api-token"
    CLOUDFLARE_TUNNEL_TOKEN = "cloudflare.tunnel-token"
    DOCKER_LOCAL_SOCKET_ACCESS_MARKER = "docker.local-socket-access-marker"
    DOCKER_REMOTE_TLS_CA_CERTIFICATE = "docker.remote-tls.ca-certificate"
    DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE = "docker.remote-tls.client-certificate"
    DOCKER_REMOTE_TLS_CLIENT_KEY = "docker.remote-tls.client-key"
    GATEWAY_PROBE_SIGNING_KEY = "gateway.probe-signing-key"
    OCI_PULL_CREDENTIAL = "oci.pull-credential"
    POSTGRES_PASSWORD = "postgres.password"
    GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY = (
        "gateway.node-control-transit-signing-key"
    )
    WORKLOAD_NODE_CONTROL_SIGNING_KEY = "workload.node-control-signing-key"


class SecretCustodyStatus(StrEnum):
    """Closed provider result states safe to return across the IO boundary."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, order=True)
class SecretProviderEndpointReference:
    """Opaque composition identity for a configured provider endpoint."""

    reference_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.reference_id, str)
            or not _ENDPOINT_REFERENCE.fullmatch(self.reference_id)
        ):
            raise SecretProviderContractError(
                "secret provider endpoint reference is malformed"
            )

    def descriptor(self) -> dict[str, str]:
        return {"reference_id": self.reference_id}


class SecretProviderEndpointReferenceCodec:
    """Strict codec for configured provider endpoint identities."""

    def encode(
        self,
        reference: SecretProviderEndpointReference,
    ) -> dict[str, str]:
        if not isinstance(reference, SecretProviderEndpointReference):
            raise SecretProviderContractError(
                "encode requires SecretProviderEndpointReference"
            )
        return reference.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> SecretProviderEndpointReference:
        if not isinstance(descriptor, Mapping):
            raise SecretProviderContractError(
                "secret provider endpoint reference must be a mapping"
            )
        if set(descriptor) != _ENDPOINT_REFERENCE_KEYS:
            raise SecretProviderContractError(
                "secret provider endpoint reference fields are invalid"
            )
        reference_id = descriptor.get("reference_id")
        if not isinstance(reference_id, str):
            raise SecretProviderContractError("reference_id must be text")
        return SecretProviderEndpointReference(reference_id)


@dataclass(frozen=True, order=True)
class SecretFilePathBinding:
    """Expose a mounted secret path through one non-secret environment slot."""

    environment_name: str

    def __post_init__(self) -> None:
        _validate_environment_name(self.environment_name)

    def descriptor(self) -> dict[str, str]:
        return {"environment_name": self.environment_name}


@dataclass(frozen=True, order=True)
class SecretProviderId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _PROVIDER_ID.fullmatch(self.value):
            raise SecretResolutionError(
                SecretResolutionCode.MALFORMED_REFERENCE,
                "secret provider identity is malformed",
            )


@dataclass(frozen=True, order=True)
class SecretReference:
    """Opaque provider-qualified identity safe for durable descriptors."""

    reference_id: str
    provider_id: SecretProviderId = field(init=False, compare=True)
    path: tuple[str, ...] = field(init=False, compare=True, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, str):
            raise SecretResolutionError(
                SecretResolutionCode.MALFORMED_REFERENCE,
                "secret reference is malformed",
            )
        parsed = urlsplit(self.reference_id)
        path = tuple(part for part in parsed.path.split("/") if part)
        if (
            parsed.scheme != "secret"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or not path
            or parsed.path != "/" + "/".join(path)
            or any(part in (".", "..") for part in path)
            or any(not _REFERENCE_SEGMENT.fullmatch(part) for part in path)
        ):
            raise SecretResolutionError(
                SecretResolutionCode.MALFORMED_REFERENCE,
                "secret reference is malformed",
            )
        object.__setattr__(self, "provider_id", SecretProviderId(parsed.netloc))
        object.__setattr__(self, "path", path)


CredentialReference = SecretReference


@dataclass(frozen=True)
class SecretResolutionGrant:
    """Committed, reference-only authority for one exact provider resolution."""

    authorization_id: str
    workspace_id: str
    reference_registration_id: str
    provider_registration_id: str
    endpoint_reference: SecretProviderEndpointReference
    credential_reference: CredentialReference
    reference: SecretReference
    intent: SecretUseIntent
    actor_subject: str
    correlation_id: str
    intent_fingerprint: str
    operation_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    activity_id: str | None = None
    effect_id: str | None = None
    probe_id: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.authorization_id, "authorization_id"),
            (self.workspace_id, "workspace_id"),
            (self.reference_registration_id, "reference_registration_id"),
            (self.provider_registration_id, "provider_registration_id"),
            (self.actor_subject, "actor_subject"),
            (self.correlation_id, "correlation_id"),
        ):
            _validate_grant_identifier(value, label)
        if not isinstance(
            self.endpoint_reference,
            SecretProviderEndpointReference,
        ):
            raise SecretProviderContractError(
                "secret resolution grant endpoint reference is malformed"
            )
        if not isinstance(self.credential_reference, SecretReference):
            raise SecretProviderContractError(
                "secret resolution grant credential reference is malformed"
            )
        if not isinstance(self.reference, SecretReference):
            raise SecretProviderContractError(
                "secret resolution grant reference is malformed"
            )
        if not isinstance(self.intent, SecretUseIntent):
            raise SecretProviderContractError(
                "secret resolution grant intent is malformed"
            )
        if (
            not isinstance(self.intent_fingerprint, str)
            or not _SHA256.fullmatch(self.intent_fingerprint)
        ):
            raise SecretProviderContractError(
                "secret resolution grant fingerprint is malformed"
            )
        for value, label in (
            (self.operation_id, "operation_id"),
            (self.session_id, "session_id"),
            (self.run_id, "run_id"),
            (self.effect_id, "effect_id"),
            (self.probe_id, "probe_id"),
        ):
            if value is not None:
                _validate_grant_identifier(value, label)
        if self.activity_id is not None:
            _validate_activity_identifier(self.activity_id)

    def permits(
        self,
        reference: SecretReference,
        intent: SecretUseIntent,
    ) -> bool:
        return self.reference == reference and self.intent is intent

    def descriptor(self) -> dict[str, object]:
        return {
            "authorization_id": self.authorization_id,
            "workspace_id": self.workspace_id,
            "reference_registration_id": self.reference_registration_id,
            "provider_registration_id": self.provider_registration_id,
            "endpoint_reference": self.endpoint_reference.reference_id,
            "credential_reference": self.credential_reference.reference_id,
            "reference_id": self.reference.reference_id,
            "intent": self.intent.value,
            "actor_subject": self.actor_subject,
            "correlation_id": self.correlation_id,
            "intent_fingerprint": self.intent_fingerprint,
            "operation_id": self.operation_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "activity_id": self.activity_id,
            "effect_id": self.effect_id,
            "probe_id": self.probe_id,
        }


@dataclass(frozen=True)
class SecretCustodyGrant:
    """Reference-only authority to write one generated value into one provider."""

    custody_id: str
    workspace_id: str
    provider_registration_id: str
    endpoint_reference: SecretProviderEndpointReference
    credential_reference: CredentialReference
    reference: SecretReference
    intent: SecretUseIntent
    actor_subject: str
    correlation_id: str
    custody_fingerprint: str
    operation_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    activity_id: str | None = None
    effect_id: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.custody_id, "custody_id"),
            (self.workspace_id, "workspace_id"),
            (self.provider_registration_id, "provider_registration_id"),
            (self.actor_subject, "actor_subject"),
            (self.correlation_id, "correlation_id"),
        ):
            _validate_grant_identifier(value, label)
        if not isinstance(
            self.endpoint_reference,
            SecretProviderEndpointReference,
        ):
            raise SecretProviderContractError(
                "secret custody grant endpoint reference is malformed"
            )
        if not isinstance(self.credential_reference, SecretReference):
            raise SecretProviderContractError(
                "secret custody grant credential reference is malformed"
            )
        if not isinstance(self.reference, SecretReference):
            raise SecretProviderContractError(
                "secret custody grant reference is malformed"
            )
        if not isinstance(self.intent, SecretUseIntent):
            raise SecretProviderContractError(
                "secret custody grant intent is malformed"
            )
        if (
            not isinstance(self.custody_fingerprint, str)
            or not _SHA256.fullmatch(self.custody_fingerprint)
        ):
            raise SecretProviderContractError(
                "secret custody grant fingerprint is malformed"
            )
        for value, label in (
            (self.operation_id, "operation_id"),
            (self.session_id, "session_id"),
            (self.run_id, "run_id"),
            (self.effect_id, "effect_id"),
        ):
            if value is not None:
                _validate_grant_identifier(value, label)
        if self.activity_id is not None:
            _validate_activity_identifier(self.activity_id)

    def permits(
        self,
        reference: SecretReference,
        intent: SecretUseIntent,
    ) -> bool:
        return self.reference == reference and self.intent is intent

    def descriptor(self) -> dict[str, object]:
        return {
            "custody_id": self.custody_id,
            "workspace_id": self.workspace_id,
            "provider_registration_id": self.provider_registration_id,
            "endpoint_reference": self.endpoint_reference.reference_id,
            "credential_reference": self.credential_reference.reference_id,
            "reference_id": self.reference.reference_id,
            "intent": self.intent.value,
            "actor_subject": self.actor_subject,
            "correlation_id": self.correlation_id,
            "custody_fingerprint": self.custody_fingerprint,
            "operation_id": self.operation_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "activity_id": self.activity_id,
            "effect_id": self.effect_id,
        }


@dataclass(frozen=True)
class SecretCustodyReceipt:
    """Secret-free identity returned after one provider custody mutation."""

    custody_id: str
    provider_registration_id: str
    reference: SecretReference
    version_id: str
    version_number: int
    status: SecretCustodyStatus = SecretCustodyStatus.ACTIVE

    def __post_init__(self) -> None:
        for value, label in (
            (self.custody_id, "custody_id"),
            (self.provider_registration_id, "provider_registration_id"),
            (self.version_id, "version_id"),
        ):
            _validate_grant_identifier(value, label)
        if not isinstance(self.reference, SecretReference):
            raise SecretProviderContractError(
                "secret custody receipt reference is malformed"
            )
        if type(self.version_number) is not int or self.version_number < 1:
            raise SecretProviderContractError(
                "secret custody receipt version number is malformed"
            )
        if not isinstance(self.status, SecretCustodyStatus):
            raise SecretProviderContractError(
                "secret custody receipt status is malformed"
            )

    def matches(self, grant: SecretCustodyGrant) -> bool:
        return (
            isinstance(grant, SecretCustodyGrant)
            and self.custody_id == grant.custody_id
            and self.provider_registration_id == grant.provider_registration_id
            and self.reference == grant.reference
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "custody_id": self.custody_id,
            "provider_registration_id": self.provider_registration_id,
            "reference_id": self.reference.reference_id,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class SecretVersionRevocationGrant:
    """Reference-only authority to revoke one exact provider secret version."""

    revocation_id: str
    workspace_id: str
    provider_registration_id: str
    endpoint_reference: SecretProviderEndpointReference
    credential_reference: CredentialReference
    reference: SecretReference
    version_id: str
    version_number: int
    actor_subject: str
    correlation_id: str
    revocation_fingerprint: str
    operation_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    activity_id: str | None = None
    effect_id: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.revocation_id, "revocation_id"),
            (self.workspace_id, "workspace_id"),
            (self.provider_registration_id, "provider_registration_id"),
            (self.version_id, "version_id"),
            (self.actor_subject, "actor_subject"),
            (self.correlation_id, "correlation_id"),
        ):
            _validate_grant_identifier(value, label)
        if not isinstance(
            self.endpoint_reference,
            SecretProviderEndpointReference,
        ):
            raise SecretProviderContractError(
                "secret version revocation endpoint reference is malformed"
            )
        if not isinstance(self.credential_reference, SecretReference):
            raise SecretProviderContractError(
                "secret version revocation credential reference is malformed"
            )
        if not isinstance(self.reference, SecretReference):
            raise SecretProviderContractError(
                "secret version revocation reference is malformed"
            )
        if type(self.version_number) is not int or self.version_number < 1:
            raise SecretProviderContractError(
                "secret version revocation version number is malformed"
            )
        if (
            not isinstance(self.revocation_fingerprint, str)
            or not _SHA256.fullmatch(self.revocation_fingerprint)
        ):
            raise SecretProviderContractError(
                "secret version revocation fingerprint is malformed"
            )
        for value, label in (
            (self.operation_id, "operation_id"),
            (self.session_id, "session_id"),
            (self.run_id, "run_id"),
            (self.effect_id, "effect_id"),
        ):
            if value is not None:
                _validate_grant_identifier(value, label)
        if self.activity_id is not None:
            _validate_activity_identifier(self.activity_id)

    def permits(
        self,
        reference: SecretReference,
        version_id: str,
        version_number: int,
    ) -> bool:
        return (
            self.reference == reference
            and self.version_id == version_id
            and self.version_number == version_number
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "revocation_id": self.revocation_id,
            "workspace_id": self.workspace_id,
            "provider_registration_id": self.provider_registration_id,
            "endpoint_reference": self.endpoint_reference.reference_id,
            "credential_reference": self.credential_reference.reference_id,
            "reference_id": self.reference.reference_id,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "actor_subject": self.actor_subject,
            "correlation_id": self.correlation_id,
            "revocation_fingerprint": self.revocation_fingerprint,
            "operation_id": self.operation_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "activity_id": self.activity_id,
            "effect_id": self.effect_id,
        }


@dataclass(frozen=True)
class SecretVersionRevocationReceipt:
    """Secret-free identity returned after exact provider-version revocation."""

    revocation_id: str
    provider_registration_id: str
    reference: SecretReference
    version_id: str
    version_number: int
    status: SecretCustodyStatus = SecretCustodyStatus.REVOKED

    def __post_init__(self) -> None:
        for value, label in (
            (self.revocation_id, "revocation_id"),
            (self.provider_registration_id, "provider_registration_id"),
            (self.version_id, "version_id"),
        ):
            _validate_grant_identifier(value, label)
        if not isinstance(self.reference, SecretReference):
            raise SecretProviderContractError(
                "secret version revocation receipt reference is malformed"
            )
        if type(self.version_number) is not int or self.version_number < 1:
            raise SecretProviderContractError(
                "secret version revocation receipt version number is malformed"
            )
        if self.status is not SecretCustodyStatus.REVOKED:
            raise SecretProviderContractError(
                "secret version revocation receipt status must be revoked"
            )

    def matches(self, grant: SecretVersionRevocationGrant) -> bool:
        return (
            isinstance(grant, SecretVersionRevocationGrant)
            and self.revocation_id == grant.revocation_id
            and self.provider_registration_id == grant.provider_registration_id
            and self.reference == grant.reference
            and self.version_id == grant.version_id
            and self.version_number == grant.version_number
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "revocation_id": self.revocation_id,
            "provider_registration_id": self.provider_registration_id,
            "reference_id": self.reference.reference_id,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "status": self.status.value,
        }


@dataclass(frozen=True, order=True)
class SecretEnvironmentDelivery:
    """Resolve one reference for one explicit use and inject it into an environment."""

    environment_name: str
    reference: SecretReference
    intent: SecretUseIntent

    def __post_init__(self) -> None:
        _validate_environment_name(self.environment_name)
        if not isinstance(self.reference, SecretReference):
            raise TypeError("secret environment delivery requires SecretReference")
        if not isinstance(self.intent, SecretUseIntent):
            raise TypeError("secret environment delivery requires SecretUseIntent")

    def descriptor(self) -> dict[str, str]:
        return {
            "kind": "environment",
            "environment_name": self.environment_name,
            "reference_id": self.reference.reference_id,
            "intent": self.intent.value,
        }


@dataclass(frozen=True, order=True)
class SecretReferenceEnvironmentDelivery:
    """Inject an opaque secret identity without resolving its referenced value."""

    environment_name: str
    reference: SecretReference

    def __post_init__(self) -> None:
        _validate_environment_name(self.environment_name)
        if not isinstance(self.reference, SecretReference):
            raise TypeError(
                "secret reference environment delivery requires SecretReference"
            )

    def descriptor(self) -> dict[str, str]:
        return {
            "kind": "environment-reference",
            "environment_name": self.environment_name,
            "reference_id": self.reference.reference_id,
        }


@dataclass(frozen=True, order=True)
class SecretFileDelivery:
    """Resolve one reference for one explicit use and mount it as a protected file."""

    target_path: str
    reference: SecretReference
    intent: SecretUseIntent
    file_mode: SecretFileMode = SecretFileMode.OWNER_READ_ONLY
    path_binding: SecretFilePathBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference, SecretReference):
            raise TypeError("secret file delivery requires SecretReference")
        if not isinstance(self.intent, SecretUseIntent):
            raise TypeError("secret file delivery requires SecretUseIntent")
        if not isinstance(self.file_mode, SecretFileMode):
            raise TypeError("secret file mode must be SecretFileMode")
        if self.path_binding is not None and not isinstance(
            self.path_binding, SecretFilePathBinding
        ):
            raise TypeError("secret file path binding must be SecretFilePathBinding")
        _validate_secret_target_path(self.target_path)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "file",
            "target_path": self.target_path,
            "reference_id": self.reference.reference_id,
            "intent": self.intent.value,
            "file_mode": self.file_mode.value,
            "path_binding": (
                None if self.path_binding is None else self.path_binding.descriptor()
            ),
        }


SecretDelivery: TypeAlias = (
    SecretEnvironmentDelivery
    | SecretReferenceEnvironmentDelivery
    | SecretFileDelivery
)


def secret_delivery_sort_key(
    value: SecretDelivery,
) -> tuple[str, str, str, str, str, str]:
    """Interpret every delivery constructor into one deterministic order."""

    match value:
        case SecretEnvironmentDelivery(
            environment_name=name,
            reference=reference,
            intent=intent,
        ):
            return ("environment", name, reference.reference_id, intent.value, "", "")
        case SecretReferenceEnvironmentDelivery(
            environment_name=name,
            reference=reference,
        ):
            return ("environment-reference", name, reference.reference_id, "", "", "")
        case SecretFileDelivery(
            target_path=path,
            reference=reference,
            intent=intent,
            file_mode=file_mode,
            path_binding=path_binding,
        ):
            return (
                "file",
                path,
                reference.reference_id,
                intent.value,
                file_mode.value,
                "" if path_binding is None else path_binding.environment_name,
            )


def secret_delivery_from_descriptor(value: Mapping[str, object]) -> SecretDelivery:
    kind = value.get("kind")
    try:
        match kind:
            case "environment" if set(value) == {
                "kind",
                "environment_name",
                "reference_id",
                "intent",
            }:
                return SecretEnvironmentDelivery(
                    _descriptor_text(value, "environment_name"),
                    SecretReference(_descriptor_text(value, "reference_id")),
                    SecretUseIntent(_descriptor_text(value, "intent")),
                )
            case "environment-reference" if set(value) == {
                "kind",
                "environment_name",
                "reference_id",
            }:
                return SecretReferenceEnvironmentDelivery(
                    _descriptor_text(value, "environment_name"),
                    SecretReference(_descriptor_text(value, "reference_id")),
                )
            case "file" if set(value) == {
                "kind",
                "target_path",
                "reference_id",
                "intent",
                "file_mode",
                "path_binding",
            }:
                return SecretFileDelivery(
                    _descriptor_text(value, "target_path"),
                    SecretReference(_descriptor_text(value, "reference_id")),
                    SecretUseIntent(_descriptor_text(value, "intent")),
                    SecretFileMode(_descriptor_text(value, "file_mode")),
                    _path_binding_from_descriptor(value.get("path_binding")),
                )
            case _:
                raise SecretResolutionError(
                    SecretResolutionCode.MALFORMED_REFERENCE,
                    "secret delivery descriptor is malformed",
                )
    except (TypeError, ValueError) as error:
        if isinstance(error, SecretResolutionError):
            raise
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret delivery descriptor is malformed",
        ) from error


def _descriptor_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret delivery descriptor is malformed",
        )
    return item


def _path_binding_from_descriptor(value: object) -> SecretFilePathBinding | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"environment_name"}:
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret delivery descriptor is malformed",
        )
    return SecretFilePathBinding(_descriptor_text(value, "environment_name"))


def _validate_environment_name(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Z][A-Z0-9_]{0,127}", value
    ):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret environment name is malformed",
        )


def _validate_secret_target_path(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("/run/secrets/"):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret file target must use the protected secret namespace",
        )
    path = PurePosixPath(value)
    if (
        str(path) != value
        or value.endswith("/")
        or any(part in (".", "..") for part in path.parts)
        or any(not _REFERENCE_SEGMENT.fullmatch(part) for part in path.parts[3:])
    ):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret file target is malformed",
        )


@dataclass(frozen=True, repr=False)
class SecretValue:
    """Ephemeral resolved text with a deliberately redacted representation."""

    _value: str

    def __post_init__(self) -> None:
        if not isinstance(self._value, str) or not self._value:
            raise SecretResolutionError(
                SecretResolutionCode.INVALID_RESOLVER_RESULT,
                "secret resolver returned an invalid value",
            )

    def reveal(self) -> str:
        """Release the value only to the bounded runtime transport boundary."""

        return self._value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


@dataclass(frozen=True)
class SecretResolved:
    reference: SecretReference
    value: SecretValue = field(repr=False)


@dataclass(frozen=True)
class SecretMissing:
    reference: SecretReference


@dataclass(frozen=True)
class SecretDenied:
    reference: SecretReference


SecretResolution: TypeAlias = SecretResolved | SecretMissing | SecretDenied


@dataclass(frozen=True)
class SecretProviderAuthority:
    """Process-bootstrap authority for one provider and path subset."""

    provider_id: SecretProviderId
    allowed_prefixes: tuple[tuple[str, ...], ...] = ((),)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, SecretProviderId):
            raise TypeError("secret provider authority requires SecretProviderId")
        if not self.allowed_prefixes or any(
            not isinstance(prefix, tuple)
            or any(not _REFERENCE_SEGMENT.fullmatch(part) for part in prefix)
            for prefix in self.allowed_prefixes
        ):
            raise TypeError("secret provider authority prefixes are malformed")

    def permits(self, reference: SecretReference) -> bool:
        return (
            reference.provider_id == self.provider_id
            and any(reference.path[: len(prefix)] == prefix for prefix in self.allowed_prefixes)
        )


class SecretResolver(Protocol):
    """Runtime authority supplied outside the deployment graph."""

    @property
    def authority(self) -> SecretProviderAuthority: ...

    def resolve(self, reference: SecretReference) -> SecretResolution: ...


class AuthorizedSecretResolver(Protocol):
    """IO authority that resolves only a committed, exact operations grant."""

    def resolve(self, grant: SecretResolutionGrant) -> SecretResolution: ...


class SecretCustodian(Protocol):
    """IO-boundary protocol for generated secret custody and exact revocation."""

    def store(
        self,
        grant: SecretCustodyGrant,
        value: SecretValue,
    ) -> SecretCustodyReceipt: ...

    def revoke(self, grant: SecretCustodyGrant) -> None: ...


@dataclass(frozen=True, repr=False)
class LocalDevelopmentSecretResolver:
    """Explicit process-memory resolver for local development and tests."""

    authority: SecretProviderAuthority
    _values: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        copied = dict(self._values)
        for reference_id, value in copied.items():
            reference = SecretReference(reference_id)
            if not self.authority.permits(reference):
                raise SecretResolutionError(
                    SecretResolutionCode.DENIED,
                    "local secret configuration exceeds bootstrap authority",
                )
            if not isinstance(value, str) or not value:
                raise SecretResolutionError(
                    SecretResolutionCode.INVALID_RESOLVER_RESULT,
                    "local secret configuration contains an invalid value",
                )
        object.__setattr__(self, "_values", MappingProxyType(copied))

    def resolve(self, reference: SecretReference) -> SecretResolution:
        if not isinstance(reference, SecretReference):
            raise TypeError("secret resolver requires SecretReference")
        if not self.authority.permits(reference):
            return SecretDenied(reference)
        value = self._values.get(reference.reference_id)
        if value is None:
            return SecretMissing(reference)
        return SecretResolved(reference, SecretValue(value))

    def __repr__(self) -> str:
        return (
            "LocalDevelopmentSecretResolver("
            f"authority={self.authority!r}, values=<redacted>)"
        )


def require_resolved_secret(
    resolver: SecretResolver,
    reference: SecretReference,
) -> SecretValue:
    """Interpret a resolver outcome without exposing secret material."""

    result = resolver.resolve(reference)
    match result:
        case SecretResolved(reference=resolved_reference, value=value) if resolved_reference == reference:
            return value
        case SecretMissing():
            raise SecretResolutionError(
                SecretResolutionCode.MISSING,
                "secret reference could not be resolved",
            )
        case SecretDenied():
            raise SecretResolutionError(
                SecretResolutionCode.DENIED,
                "secret reference is outside bootstrap authority",
            )
        case _:
            raise SecretResolutionError(
                SecretResolutionCode.INVALID_RESOLVER_RESULT,
                "secret resolver returned an invalid result",
            )


def require_authorized_secret(
    resolver: AuthorizedSecretResolver,
    grant: SecretResolutionGrant,
) -> SecretValue:
    """Interpret one grant-bound resolution without exposing secret material."""

    if not isinstance(grant, SecretResolutionGrant):
        raise SecretResolutionError(
            SecretResolutionCode.DENIED,
            "secret resolution requires committed authorization",
        )
    result = resolver.resolve(grant)
    match result:
        case SecretResolved(reference=resolved_reference, value=value) if (
            resolved_reference == grant.reference
        ):
            return value
        case SecretMissing():
            raise SecretResolutionError(
                SecretResolutionCode.MISSING,
                "secret reference could not be resolved",
            )
        case SecretDenied():
            raise SecretResolutionError(
                SecretResolutionCode.DENIED,
                "secret resolution grant was denied",
            )
        case _:
            raise SecretResolutionError(
                SecretResolutionCode.INVALID_RESOLVER_RESULT,
                "authorized secret resolver returned an invalid result",
            )


def _validate_grant_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or not _GRANT_IDENTIFIER.fullmatch(value):
        raise SecretProviderContractError(
            f"secret resolution grant {label} is malformed"
        )


def _validate_activity_identifier(value: object) -> None:
    if not _is_canonical_activity_identity(value):
        raise SecretProviderContractError(
            "secret resolution grant activity_id is malformed"
        )
