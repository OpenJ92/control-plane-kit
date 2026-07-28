"""Durable named public ingress authority admission for operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import re
from typing import Any, Mapping, Protocol

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
    SecretValue,
)


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HOST_PATTERN_LABEL = re.compile(r"^[a-z0-9*](?:[a-z0-9-*]{0,61}[a-z0-9*])?$")
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "private_key",
    "private-key",
    "api_key",
    "apikey",
    "credential",
)


class IngressAuthorityRegistrationError(ValueError):
    """Raised when ingress authority registration data is malformed."""


class IngressAuthorityRegistrationConflict(IngressAuthorityRegistrationError):
    """Raised when authority replacement requires an explicit decision."""


class OwnedIngressResourceConflict(IngressAuthorityRegistrationError):
    """Raised when owned ingress evidence conflicts with existing truth."""


class GeneratedSecretRecordingConflict(IngressAuthorityRegistrationError):
    """Raised when generated secret evidence conflicts with existing truth."""


class IngressAuthorityAuthorizationDenied(IngressAuthorityRegistrationError):
    """Raised when an actor lacks a focused ingress authority scope."""


class IngressAuthorityNotFound(IngressAuthorityRegistrationError):
    """Raised when an ingress authority cannot be found."""


class IngressAuthorityProviderKind(StrEnum):
    """Closed provider kinds supported by operations."""

    CLOUDFLARE = "cloudflare"


class RegisteredIngressAuthorityStatus(StrEnum):
    """Closed durable status for workspace ingress authority registration."""

    ACTIVE = "active"
    REVOKED = "revoked"


class CloudflareIngressTeardownActionKind(StrEnum):
    """Closed Cloudflare cleanup actions derived from owned evidence."""

    DELETE_DNS_RECORD = "delete-dns-record"
    DELETE_TUNNEL = "delete-tunnel"
    SKIP_RETAINED_OR_EXTERNAL = "skip-retained-or-external"


class CloudflareTunnelTokenDeliveryStep(StrEnum):
    """Closed ordering steps for generated tunnel-token delivery."""

    ALLOCATE_NAMED_INGRESS = "allocate-named-ingress"
    RECORD_TUNNEL_TOKEN_SECRET = "record-tunnel-token-secret"
    START_CLOUDFLARED_CONNECTOR = "start-cloudflared-connector"


class GeneratedSecretPurpose(StrEnum):
    """Closed purposes for generated runtime secrets recorded by operations."""

    CLOUDFLARED_TUNNEL_TOKEN = "cloudflared-tunnel-token"


class GeneratedSecretRecorder(Protocol):
    """Boundary that accepts raw generated secrets and returns safe references."""

    def record_generated_secret(
        self,
        *,
        workspace_id: str,
        purpose: GeneratedSecretPurpose,
        source_run_id: str,
        source_activity_id: str,
        source_event_id: str,
        secret_value: SecretValue,
    ) -> SecretReference: ...


@dataclass(frozen=True)
class CloudflareZoneIngressAuthority:
    """Authority to allocate named public ingress inside one Cloudflare zone."""

    account_id: str
    zone_id: str
    zone_name: str
    api_token_ref: SecretReference = field(repr=False)
    allowed_hostname_pattern: str

    def __post_init__(self) -> None:
        _validate_identifier(self.account_id, "Cloudflare account_id")
        _validate_identifier(self.zone_id, "Cloudflare zone_id")
        _validate_zone_name(self.zone_name)
        _require_secret_reference(self.api_token_ref, "api_token_ref")
        _validate_hostname_pattern(
            self.allowed_hostname_pattern,
            zone_name=self.zone_name,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "provider_kind": IngressAuthorityProviderKind.CLOUDFLARE.value,
            "account_id": self.account_id,
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "api_token_ref": self.api_token_ref.reference_id,
            "allowed_hostname_pattern": self.allowed_hostname_pattern,
        }

    def storage_descriptor(self) -> dict[str, object]:
        return self.descriptor()

    def allows_hostname(self, hostname: str) -> bool:
        try:
            _validate_hostname(hostname)
        except IngressAuthorityRegistrationError:
            return False
        pattern = re.escape(self.allowed_hostname_pattern.lower()).replace(
            r"\*",
            r"[a-z0-9-]+",
        )
        return re.fullmatch(pattern, hostname.lower()) is not None


IngressAuthority = CloudflareZoneIngressAuthority


@dataclass(frozen=True)
class CloudflareOwnedIngressResource:
    """Bounded evidence for Cloudflare resources allocated by one CPK activity."""

    workspace_id: str
    runtime_id: str
    ingress_id: str
    authority_ref: IngressAuthorityReference
    provider_kind: IngressAuthorityProviderKind
    tunnel_name: str
    tunnel_id: str
    dns_record_id: str
    hostname: str
    zone_id: str
    lifecycle: PublicIngressLifecycle
    created_at: str
    observed_at: str
    source_run_id: str
    source_activity_id: str
    source_event_id: str

    def __post_init__(self) -> None:
        _validate_identifier(self.workspace_id, "workspace_id")
        _validate_identifier(self.runtime_id, "runtime_id")
        _validate_identifier(self.ingress_id, "ingress_id")
        if not isinstance(self.authority_ref, IngressAuthorityReference):
            raise IngressAuthorityRegistrationError(
                "Cloudflare ingress resource requires IngressAuthorityReference"
            )
        if self.provider_kind is not IngressAuthorityProviderKind.CLOUDFLARE:
            raise IngressAuthorityRegistrationError(
                "Cloudflare ingress resource provider kind must be cloudflare"
            )
        _validate_identifier(self.tunnel_name, "tunnel_name")
        _validate_identifier(self.tunnel_id, "tunnel_id")
        _validate_identifier(self.dns_record_id, "dns_record_id")
        _validate_hostname(self.hostname)
        _validate_identifier(self.zone_id, "zone_id")
        if not isinstance(self.lifecycle, PublicIngressLifecycle):
            raise IngressAuthorityRegistrationError(
                "Cloudflare ingress resource lifecycle must be closed"
            )
        _validate_identifier(self.created_at, "created_at")
        _validate_identifier(self.observed_at, "observed_at")
        _validate_identifier(self.source_run_id, "source_run_id")
        _validate_identifier(self.source_activity_id, "source_activity_id")
        _validate_identifier(self.source_event_id, "source_event_id")
        if any(marker in repr(self.descriptor()).lower() for marker in _SECRET_MARKERS):
            raise IngressAuthorityRegistrationError(
                "Cloudflare ingress resource evidence must be secret-free"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "runtime_id": self.runtime_id,
            "ingress_id": self.ingress_id,
            "authority_ref": self.authority_ref.reference_id,
            "provider_kind": self.provider_kind.value,
            "tunnel_name": self.tunnel_name,
            "tunnel_id": self.tunnel_id,
            "dns_record_id": self.dns_record_id,
            "hostname": self.hostname,
            "zone_id": self.zone_id,
            "lifecycle": self.lifecycle.value,
            "created_at": self.created_at,
            "observed_at": self.observed_at,
            "source_run_id": self.source_run_id,
            "source_activity_id": self.source_activity_id,
            "source_event_id": self.source_event_id,
        }


@dataclass(frozen=True)
class CloudflareIngressTeardownAction:
    """One bounded Cloudflare cleanup action derived from recorded ids."""

    kind: CloudflareIngressTeardownActionKind
    resource_id: str | None = None
    hostname: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CloudflareIngressTeardownActionKind):
            raise IngressAuthorityRegistrationError(
                "Cloudflare teardown action kind must be closed"
            )
        if self.resource_id is not None:
            _validate_identifier(self.resource_id, "resource_id")
        if self.hostname is not None:
            _validate_hostname(self.hostname)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "resource_id": self.resource_id,
            "hostname": self.hostname,
        }


@dataclass(frozen=True)
class CloudflareIngressTeardownPlan:
    """Fail-closed cleanup plan; no broad Cloudflare search is implied."""

    resource: CloudflareOwnedIngressResource
    actions: tuple[CloudflareIngressTeardownAction, ...]

    def descriptor(self) -> dict[str, object]:
        return {
            "resource": self.resource.descriptor(),
            "actions": [action.descriptor() for action in self.actions],
        }


@dataclass(frozen=True)
class CloudflareTunnelTokenDeliveryPlan:
    """Secret-reference delivery plan for a generated cloudflared tunnel token."""

    resource: CloudflareOwnedIngressResource
    connector_node_id: str
    secret_delivery: SecretEnvironmentDelivery
    ordering: tuple[CloudflareTunnelTokenDeliveryStep, ...] = (
        CloudflareTunnelTokenDeliveryStep.ALLOCATE_NAMED_INGRESS,
        CloudflareTunnelTokenDeliveryStep.RECORD_TUNNEL_TOKEN_SECRET,
        CloudflareTunnelTokenDeliveryStep.START_CLOUDFLARED_CONNECTOR,
    )

    def __post_init__(self) -> None:
        _validate_identifier(self.connector_node_id, "connector_node_id")
        if not isinstance(self.resource, CloudflareOwnedIngressResource):
            raise IngressAuthorityRegistrationError(
                "Cloudflare tunnel token delivery requires owned resource evidence"
            )
        _validate_tunnel_token_delivery(self.secret_delivery)
        if self.ordering != (
            CloudflareTunnelTokenDeliveryStep.ALLOCATE_NAMED_INGRESS,
            CloudflareTunnelTokenDeliveryStep.RECORD_TUNNEL_TOKEN_SECRET,
            CloudflareTunnelTokenDeliveryStep.START_CLOUDFLARED_CONNECTOR,
        ):
            raise IngressAuthorityRegistrationError(
                "Cloudflare tunnel token delivery ordering is unsupported"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "resource": self.resource.descriptor(),
            "connector_node_id": self.connector_node_id,
            "secret_delivery": self.secret_delivery.descriptor(),
            "ordering": [step.value for step in self.ordering],
        }


@dataclass(frozen=True)
class GeneratedIngressSecretReference:
    """Durable secret-reference evidence for a provider-generated ingress secret."""

    workspace_id: str
    purpose: GeneratedSecretPurpose
    secret_ref: SecretReference
    recorded_at: str
    source_run_id: str
    source_activity_id: str
    source_event_id: str

    def __post_init__(self) -> None:
        _validate_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.purpose, GeneratedSecretPurpose):
            raise IngressAuthorityRegistrationError(
                "generated secret purpose must be closed"
            )
        _require_secret_reference(self.secret_ref, "secret_ref")
        _validate_identifier(self.recorded_at, "recorded_at")
        _validate_identifier(self.source_run_id, "source_run_id")
        _validate_identifier(self.source_activity_id, "source_activity_id")
        _validate_identifier(self.source_event_id, "source_event_id")

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "purpose": self.purpose.value,
            "secret_ref": self.secret_ref.reference_id,
            "recorded_at": self.recorded_at,
            "source_run_id": self.source_run_id,
            "source_activity_id": self.source_activity_id,
            "source_event_id": self.source_event_id,
        }


class InMemoryGeneratedSecretRecorder:
    """Development recorder until generated secrets move to a dedicated product."""

    def __init__(self, reference_root: str = "secret://generated/ingress") -> None:
        root = SecretReference(f"{reference_root}/root")
        self._reference_root = "/".join((root.reference_id.rsplit("/", 1)[0],))
        self._values: dict[SecretReference, SecretValue] = {}

    def record_generated_secret(
        self,
        *,
        workspace_id: str,
        purpose: GeneratedSecretPurpose,
        source_run_id: str,
        source_activity_id: str,
        source_event_id: str,
        secret_value: SecretValue,
    ) -> SecretReference:
        _validate_identifier(workspace_id, "workspace_id")
        if not isinstance(purpose, GeneratedSecretPurpose):
            raise IngressAuthorityRegistrationError(
                "generated secret purpose must be closed"
            )
        _validate_identifier(source_run_id, "source_run_id")
        _validate_identifier(source_activity_id, "source_activity_id")
        _validate_identifier(source_event_id, "source_event_id")
        if not isinstance(secret_value, SecretValue):
            raise IngressAuthorityRegistrationError(
                "generated secret recorder requires SecretValue"
            )
        reference = SecretReference(
            "/".join(
                (
                    self._reference_root,
                    workspace_id,
                    purpose.value,
                    source_run_id,
                    source_activity_id,
                    source_event_id,
                )
            )
        )
        existing = self._values.get(reference)
        if existing is not None and existing.reveal() != secret_value.reveal():
            raise GeneratedSecretRecordingConflict(
                "generated secret replacement requires explicit policy"
            )
        self._values[reference] = secret_value
        return reference

    def resolve_generated_secret(self, reference: SecretReference) -> SecretValue:
        _require_secret_reference(reference, "reference")
        try:
            return self._values[reference]
        except KeyError as error:
            raise IngressAuthorityRegistrationError(
                "generated secret reference was not found"
            ) from error


def record_generated_ingress_secret(
    *,
    recorder: GeneratedSecretRecorder,
    workspace_id: str,
    purpose: GeneratedSecretPurpose,
    source_run_id: str,
    source_activity_id: str,
    source_event_id: str,
    recorded_at: str,
    secret_value: SecretValue,
) -> GeneratedIngressSecretReference:
    """Record a raw provider result and return durable reference-only evidence."""

    secret_ref = recorder.record_generated_secret(
        workspace_id=workspace_id,
        purpose=purpose,
        source_run_id=source_run_id,
        source_activity_id=source_activity_id,
        source_event_id=source_event_id,
        secret_value=secret_value,
    )
    return GeneratedIngressSecretReference(
        workspace_id=workspace_id,
        purpose=purpose,
        secret_ref=secret_ref,
        recorded_at=recorded_at,
        source_run_id=source_run_id,
        source_activity_id=source_activity_id,
        source_event_id=source_event_id,
    )


def cloudflare_ingress_teardown_plan(
    *,
    authority: CloudflareZoneIngressAuthority,
    resource: CloudflareOwnedIngressResource | None,
) -> CloudflareIngressTeardownPlan:
    """Return the precise teardown plan permitted by recorded ownership evidence."""

    if resource is None:
        raise IngressAuthorityRegistrationError(
            "Cloudflare ingress teardown requires ownership evidence"
        )
    _validate_ingress_resource_against_authority(authority=authority, resource=resource)
    if not resource.tunnel_name.startswith("cpk-"):
        raise IngressAuthorityRegistrationError(
            "Cloudflare ingress tunnel ownership is ambiguous"
        )
    if resource.lifecycle is not PublicIngressLifecycle.EPHEMERAL:
        return CloudflareIngressTeardownPlan(
            resource=resource,
            actions=(
                CloudflareIngressTeardownAction(
                    CloudflareIngressTeardownActionKind.SKIP_RETAINED_OR_EXTERNAL,
                    hostname=resource.hostname,
                ),
            ),
        )
    return CloudflareIngressTeardownPlan(
        resource=resource,
        actions=(
            CloudflareIngressTeardownAction(
                CloudflareIngressTeardownActionKind.DELETE_DNS_RECORD,
                resource_id=resource.dns_record_id,
                hostname=resource.hostname,
            ),
            CloudflareIngressTeardownAction(
                CloudflareIngressTeardownActionKind.DELETE_TUNNEL,
                resource_id=resource.tunnel_id,
                hostname=resource.hostname,
            ),
        ),
    )


def cloudflare_tunnel_token_delivery_plan(
    *,
    authority: CloudflareZoneIngressAuthority,
    resource: CloudflareOwnedIngressResource,
    connector_node_id: str,
    tunnel_token_ref: SecretReference,
) -> CloudflareTunnelTokenDeliveryPlan:
    """Plan explicit delivery of a generated tunnel token to cloudflared."""

    _validate_ingress_resource_against_authority(authority=authority, resource=resource)
    return CloudflareTunnelTokenDeliveryPlan(
        resource=resource,
        connector_node_id=connector_node_id,
        secret_delivery=SecretEnvironmentDelivery("TUNNEL_TOKEN", tunnel_token_ref),
    )


def require_cloudflared_tunnel_token_delivery(
    deliveries: tuple[SecretEnvironmentDelivery, ...],
) -> SecretEnvironmentDelivery:
    """Return the explicit tunnel-token delivery or fail before connector start."""

    if not isinstance(deliveries, tuple):
        raise IngressAuthorityRegistrationError(
            "cloudflared connector secret deliveries must be a tuple"
        )
    matches = tuple(
        delivery
        for delivery in deliveries
        if isinstance(delivery, SecretEnvironmentDelivery)
        and delivery.environment_name == "TUNNEL_TOKEN"
    )
    if len(matches) != 1:
        raise IngressAuthorityRegistrationError(
            "cloudflared connector requires exactly one TUNNEL_TOKEN delivery"
        )
    _validate_tunnel_token_delivery(matches[0])
    return matches[0]


def _validate_ingress_resource_against_authority(
    *,
    authority: CloudflareZoneIngressAuthority,
    resource: CloudflareOwnedIngressResource,
) -> None:
    if not isinstance(authority, CloudflareZoneIngressAuthority):
        raise IngressAuthorityRegistrationError(
            "Cloudflare ingress authority is required"
        )
    if not isinstance(resource, CloudflareOwnedIngressResource):
        raise IngressAuthorityRegistrationError(
            "Cloudflare ingress owned resource evidence is required"
        )
    if resource.zone_id != authority.zone_id:
        raise IngressAuthorityRegistrationError(
            "Cloudflare ingress ownership zone does not match authority"
        )
    if not authority.allows_hostname(resource.hostname):
        raise IngressAuthorityRegistrationError(
            "Cloudflare ingress ownership hostname is outside authority policy"
        )


def _validate_tunnel_token_delivery(delivery: object) -> None:
    if not isinstance(delivery, SecretEnvironmentDelivery):
        raise IngressAuthorityRegistrationError(
            "cloudflared tunnel token delivery must use SecretEnvironmentDelivery"
        )
    if delivery.environment_name != "TUNNEL_TOKEN":
        raise IngressAuthorityRegistrationError(
            "cloudflared tunnel token delivery must target TUNNEL_TOKEN"
        )
    _require_secret_reference(delivery.reference, "tunnel_token_ref")


class CloudflareZoneIngressAuthorityCodec:
    """Strict storage codec for Cloudflare ingress authorities."""

    def encode(self, authority: IngressAuthority) -> dict[str, object]:
        if not isinstance(authority, CloudflareZoneIngressAuthority):
            raise IngressAuthorityRegistrationError("unsupported ingress authority")
        return authority.storage_descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> IngressAuthority:
        mapping = _mapping(descriptor, "ingress authority")
        _require_keys(
            mapping,
            frozenset(
                {
                    "provider_kind",
                    "account_id",
                    "zone_id",
                    "zone_name",
                    "api_token_ref",
                    "allowed_hostname_pattern",
                }
            ),
            "Cloudflare ingress authority",
        )
        provider_kind = _text(mapping, "provider_kind")
        if provider_kind != IngressAuthorityProviderKind.CLOUDFLARE.value:
            raise IngressAuthorityRegistrationError(
                "unsupported ingress authority provider"
            )
        return CloudflareZoneIngressAuthority(
            account_id=_text(mapping, "account_id"),
            zone_id=_text(mapping, "zone_id"),
            zone_name=_text(mapping, "zone_name"),
            api_token_ref=SecretReference(_text(mapping, "api_token_ref")),
            allowed_hostname_pattern=_text(mapping, "allowed_hostname_pattern"),
        )


@dataclass(frozen=True)
class RegisteredIngressAuthority:
    """An ingress authority admitted as workspace operational truth."""

    registration_id: str
    workspace_id: str
    authority_ref: IngressAuthorityReference
    authority: IngressAuthority
    admitted_by: str
    admitted_at: str
    status: RegisteredIngressAuthorityStatus = RegisteredIngressAuthorityStatus.ACTIVE
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.registration_id, "registration_id")
        _validate_identifier(self.workspace_id, "workspace_id")
        _validate_identifier(self.admitted_by, "admitted_by")
        _validate_identifier(self.admitted_at, "admitted_at")
        if not isinstance(self.authority_ref, IngressAuthorityReference):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority requires IngressAuthorityReference"
            )
        if not isinstance(self.authority, CloudflareZoneIngressAuthority):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority is unsupported"
            )
        if not isinstance(self.status, RegisteredIngressAuthorityStatus):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority status is unsupported"
            )
        if not isinstance(self.metadata, Mapping):
            raise IngressAuthorityRegistrationError(
                "registered ingress authority metadata must be mapping"
            )

    @property
    def provider_kind(self) -> IngressAuthorityProviderKind:
        return IngressAuthorityProviderKind.CLOUDFLARE

    def descriptor(self) -> dict[str, object]:
        return {
            "registration_id": self.registration_id,
            "workspace_id": self.workspace_id,
            "authority_ref": self.authority_ref.reference_id,
            "provider_kind": self.provider_kind.value,
            "authority": self.authority.descriptor(),
            "admitted_by": self.admitted_by,
            "admitted_at": self.admitted_at,
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_authority(
        cls,
        *,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
        authority: IngressAuthority,
        admitted_by: str,
        admitted_at: str,
    ) -> "RegisteredIngressAuthority":
        return cls(
            registration_id=ingress_authority_registration_id_for(
                workspace_id,
                authority_ref,
                authority,
            ),
            workspace_id=workspace_id,
            authority_ref=authority_ref,
            authority=authority,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )


@dataclass(frozen=True)
class RegisterIngressAuthorityCommand:
    """Application command to admit one named ingress authority."""

    workspace_id: str
    authority_ref: IngressAuthorityReference
    authority: IngressAuthority
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        RegisteredIngressAuthority.from_authority(
            workspace_id=self.workspace_id,
            authority_ref=self.authority_ref,
            authority=self.authority,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
        )


@dataclass(frozen=True)
class RevokeIngressAuthorityCommand:
    """Application command to revoke one workspace ingress authority."""

    workspace_id: str
    authority_ref: IngressAuthorityReference
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.authority_ref, IngressAuthorityReference):
            raise IngressAuthorityRegistrationError(
                "revoke requires IngressAuthorityReference"
            )
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


class IngressAuthorityRegistrationService:
    """Application service owning ingress authority transaction boundaries."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register(
        self,
        command: RegisterIngressAuthorityCommand,
    ) -> RegisteredIngressAuthority:
        if not isinstance(command, RegisterIngressAuthorityCommand):
            raise IngressAuthorityRegistrationError(
                "register requires RegisterIngressAuthorityCommand"
            )
        if PolicyScope.INGRESS_AUTHORITY_REGISTER not in command.actor_scopes:
            raise IngressAuthorityAuthorizationDenied(
                "ingress authority registration requires ingress-authority:register"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.ingress_authorities.register(
                workspace_id=command.workspace_id,
                authority_ref=command.authority_ref,
                authority=command.authority,
                admitted_by=command.admitted_by,
                admitted_at=command.admitted_at,
            )
            unit_of_work.commit()
            return registered

    def revoke(
        self,
        command: RevokeIngressAuthorityCommand,
    ) -> RegisteredIngressAuthority:
        if not isinstance(command, RevokeIngressAuthorityCommand):
            raise IngressAuthorityRegistrationError(
                "revoke requires RevokeIngressAuthorityCommand"
            )
        if PolicyScope.INGRESS_AUTHORITY_REVOKE not in command.actor_scopes:
            raise IngressAuthorityAuthorizationDenied(
                "ingress authority revocation requires ingress-authority:revoke"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.ingress_authorities.revoke(
                command.workspace_id,
                command.authority_ref,
            )
            unit_of_work.commit()
            return registered


def ingress_authority_registration_id_for(
    workspace_id: str,
    authority_ref: IngressAuthorityReference,
    authority: IngressAuthority,
) -> str:
    """Return deterministic identity for one ingress authority admission."""

    _validate_identifier(workspace_id, "workspace_id")
    if not isinstance(authority_ref, IngressAuthorityReference):
        raise IngressAuthorityRegistrationError(
            "ingress authority id requires IngressAuthorityReference"
        )
    encoded = CloudflareZoneIngressAuthorityCodec().encode(authority)
    digest = sha256(
        repr((workspace_id, authority_ref.reference_id, encoded)).encode("utf-8")
    ).hexdigest()
    return f"iauth_{digest}"


def _credential_references(authority: RegisteredIngressAuthority) -> dict[str, object]:
    return {"api_token_ref": authority.authority.api_token_ref.reference_id}


def _require_secret_reference(value: object, field: str) -> None:
    if not isinstance(value, SecretReference):
        raise IngressAuthorityRegistrationError(f"{field} requires SecretReference")


def _validate_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise IngressAuthorityRegistrationError(f"{field} must be nonempty and bounded")
    if any(ord(character) < 32 for character in value):
        raise IngressAuthorityRegistrationError(
            f"{field} must not contain control characters"
        )


def _validate_zone_name(value: str) -> None:
    _validate_hostname(value)
    if value != value.lower():
        raise IngressAuthorityRegistrationError("zone name must be lowercase")


def _validate_hostname(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise IngressAuthorityRegistrationError("hostname must be nonempty and bounded")
    if value.endswith(".") or value.startswith("."):
        raise IngressAuthorityRegistrationError("hostname must not have empty labels")
    labels = value.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise IngressAuthorityRegistrationError("hostname is malformed")


def _validate_hostname_pattern(pattern: str, *, zone_name: str) -> None:
    if not isinstance(pattern, str) or not pattern:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must be nonempty"
        )
    lowered = pattern.lower()
    if pattern != lowered:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must be lowercase"
        )
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must not contain secret-shaped text"
        )
    if lowered.count("*") != 1:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern requires exactly one wildcard"
        )
    suffix = f".{zone_name}"
    if not lowered.endswith(suffix):
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must be inside the configured zone"
        )
    labels = lowered.split(".")
    if len(labels) != len(zone_name.split(".")) + 1:
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must authorize one hostname label in the zone"
        )
    if labels[0] == "*":
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern must not authorize the whole zone"
        )
    if not all(_HOST_PATTERN_LABEL.fullmatch(label) for label in labels):
        raise IngressAuthorityRegistrationError(
            "allowed hostname pattern is malformed"
        )


def _scopes(value: tuple[PolicyScope, ...]) -> tuple[PolicyScope, ...]:
    if not isinstance(value, tuple):
        raise IngressAuthorityRegistrationError(
            "actor_scopes must be a tuple of PolicyScope"
        )
    if not all(isinstance(scope, PolicyScope) for scope in value):
        raise IngressAuthorityRegistrationError(
            "actor_scopes must contain only PolicyScope"
        )
    return value


def _mapping(value: object, field: str = "mapping") -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise IngressAuthorityRegistrationError(f"{field} must be a mapping")
    return value


def _require_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    field: str,
) -> None:
    keys = frozenset(mapping)
    if keys != expected:
        extra = sorted(keys - expected)
        missing = sorted(expected - keys)
        details: list[str] = []
        if extra:
            details.append(f"unknown keys: {', '.join(extra)}")
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        raise IngressAuthorityRegistrationError(
            f"invalid {field}; " + "; ".join(details)
        )


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise IngressAuthorityRegistrationError(f"{key} must be a string")
    return value
