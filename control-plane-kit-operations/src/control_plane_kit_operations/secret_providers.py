"""Durable provider-neutral secret admission for operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_SECRET_REFERENCE_PREFIX = re.compile(
    r"^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$"
)
_SECRET_SHAPED_METADATA_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "api_token",
        "ciphertext",
        "credential",
        "credential_value",
        "master_key",
        "password",
        "plaintext",
        "private_key",
        "secret",
        "secret_value",
        "token",
    }
)
_SECRET_VALUE_MARKERS = (
    "://",
    "bearer ",
    "-----begin",
    "ciphertext",
    "plaintext",
)


class SecretProviderRegistrationError(ValueError):
    """Raised when provider or reference admission is malformed."""


class SecretProviderRegistrationConflict(SecretProviderRegistrationError):
    """Raised when replacement lacks exact supersession evidence."""


class SecretProviderAuthorizationDenied(SecretProviderRegistrationError):
    """Raised when an actor lacks a focused provider admission scope."""


class SecretProviderNotFound(SecretProviderRegistrationError):
    """Raised when provider or reference admission cannot be selected."""


class SecretProviderKind(StrEnum):
    """Closed provider implementations currently admitted by operations."""

    CONTROL_PLANE_KIT_SECRETS = "control-plane-kit-secrets"


class RegisteredSecretProviderStatus(StrEnum):
    """Durable provider registration lifecycle."""

    ACTIVE = "active"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class RegisteredSecretReferenceStatus(StrEnum):
    """Durable pre-existing provider-handle lifecycle."""

    ACTIVE = "active"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class RegisteredSecretProvider:
    """One workspace's admitted provider metadata, never provider material."""

    registration_id: str
    workspace_id: str
    provider_id: SecretProviderId
    provider_kind: SecretProviderKind
    display_name: str
    endpoint_reference: SecretProviderEndpointReference
    credential_reference: SecretReference
    allowed_reference_prefixes: tuple[SecretReference, ...]
    allowed_intents: tuple[SecretUseIntent, ...]
    admitted_by: str
    admitted_at: str
    status: RegisteredSecretProviderStatus = RegisteredSecretProviderStatus.ACTIVE
    supersedes_registration_id: str | None = None
    revoked_by: str | None = None
    revoked_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.registration_id, "registration_id")
        _require_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.provider_id, SecretProviderId):
            raise SecretProviderRegistrationError(
                "registered provider requires SecretProviderId"
            )
        if not isinstance(self.provider_kind, SecretProviderKind):
            raise SecretProviderRegistrationError(
                "registered provider kind is unsupported"
            )
        _require_public_text(self.display_name, "display_name", maximum=128)
        if not isinstance(
            self.endpoint_reference,
            SecretProviderEndpointReference,
        ):
            raise SecretProviderRegistrationError(
                "registered provider requires endpoint reference"
            )
        _require_secret_reference(
            self.credential_reference,
            "credential_reference",
        )
        prefixes = _reference_prefixes(
            self.allowed_reference_prefixes,
            self.provider_id,
        )
        intents = _intents(self.allowed_intents)
        _require_identifier(self.admitted_by, "admitted_by")
        _require_bounded_text(self.admitted_at, "admitted_at", maximum=128)
        if not isinstance(self.status, RegisteredSecretProviderStatus):
            raise SecretProviderRegistrationError(
                "registered provider status is unsupported"
            )
        if self.supersedes_registration_id is not None:
            _require_identifier(
                self.supersedes_registration_id,
                "supersedes_registration_id",
            )
            if self.supersedes_registration_id == self.registration_id:
                raise SecretProviderRegistrationError(
                    "provider registration cannot supersede itself"
                )
        _validate_revocation_evidence(
            self.status is RegisteredSecretProviderStatus.REVOKED,
            self.revoked_by,
            self.revoked_at,
        )
        metadata = _metadata(self.metadata)
        object.__setattr__(self, "allowed_reference_prefixes", prefixes)
        object.__setattr__(self, "allowed_intents", intents)
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

    def descriptor(self) -> dict[str, object]:
        return {
            "registration_id": self.registration_id,
            "workspace_id": self.workspace_id,
            "provider_id": self.provider_id.value,
            "provider_kind": self.provider_kind.value,
            "display_name": self.display_name,
            "endpoint_reference": self.endpoint_reference.reference_id,
            "credential_reference": self.credential_reference.reference_id,
            "allowed_reference_prefixes": [
                reference.reference_id
                for reference in self.allowed_reference_prefixes
            ],
            "allowed_intents": [intent.value for intent in self.allowed_intents],
            "admitted_by": self.admitted_by,
            "admitted_at": self.admitted_at,
            "status": self.status.value,
            "supersedes_registration_id": self.supersedes_registration_id,
            "revoked_by": self.revoked_by,
            "revoked_at": self.revoked_at,
            "metadata": dict(self.metadata),
        }

    def same_admission_as(self, other: "RegisteredSecretProvider") -> bool:
        return _provider_semantics(self) == _provider_semantics(other)


@dataclass(frozen=True)
class RegisteredSecretReference:
    """One pre-existing provider handle admitted for bounded use."""

    registration_id: str
    workspace_id: str
    reference: SecretReference
    provider_registration_id: str
    allowed_intents: tuple[SecretUseIntent, ...]
    admitted_by: str
    admitted_at: str
    status: RegisteredSecretReferenceStatus = RegisteredSecretReferenceStatus.ACTIVE
    supersedes_registration_id: str | None = None
    revoked_by: str | None = None
    revoked_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.registration_id, "registration_id")
        _require_identifier(self.workspace_id, "workspace_id")
        _require_secret_reference(self.reference, "reference")
        _require_identifier(
            self.provider_registration_id,
            "provider_registration_id",
        )
        intents = _intents(self.allowed_intents)
        _require_identifier(self.admitted_by, "admitted_by")
        _require_bounded_text(self.admitted_at, "admitted_at", maximum=128)
        if not isinstance(self.status, RegisteredSecretReferenceStatus):
            raise SecretProviderRegistrationError(
                "registered secret reference status is unsupported"
            )
        if self.supersedes_registration_id is not None:
            _require_identifier(
                self.supersedes_registration_id,
                "supersedes_registration_id",
            )
            if self.supersedes_registration_id == self.registration_id:
                raise SecretProviderRegistrationError(
                    "secret reference registration cannot supersede itself"
                )
        _validate_revocation_evidence(
            self.status is RegisteredSecretReferenceStatus.REVOKED,
            self.revoked_by,
            self.revoked_at,
        )
        metadata = _metadata(self.metadata)
        object.__setattr__(self, "allowed_intents", intents)
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

    def descriptor(self) -> dict[str, object]:
        return {
            "registration_id": self.registration_id,
            "workspace_id": self.workspace_id,
            "reference_id": self.reference.reference_id,
            "provider_registration_id": self.provider_registration_id,
            "allowed_intents": [intent.value for intent in self.allowed_intents],
            "admitted_by": self.admitted_by,
            "admitted_at": self.admitted_at,
            "status": self.status.value,
            "supersedes_registration_id": self.supersedes_registration_id,
            "revoked_by": self.revoked_by,
            "revoked_at": self.revoked_at,
            "metadata": dict(self.metadata),
        }

    def same_admission_as(self, other: "RegisteredSecretReference") -> bool:
        return _reference_semantics(self) == _reference_semantics(other)


@dataclass(frozen=True)
class RegisterSecretProviderCommand:
    workspace_id: str
    provider_id: SecretProviderId
    provider_kind: SecretProviderKind
    display_name: str
    endpoint_reference: SecretProviderEndpointReference
    credential_reference: SecretReference
    allowed_reference_prefixes: tuple[SecretReference, ...]
    allowed_intents: tuple[SecretUseIntent, ...]
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]
    supersedes_registration_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        self.candidate()

    def candidate(self) -> RegisteredSecretProvider:
        registration_id = secret_provider_registration_id_for(
            workspace_id=self.workspace_id,
            provider_id=self.provider_id,
            provider_kind=self.provider_kind,
            display_name=self.display_name,
            endpoint_reference=self.endpoint_reference,
            credential_reference=self.credential_reference,
            allowed_reference_prefixes=self.allowed_reference_prefixes,
            allowed_intents=self.allowed_intents,
            supersedes_registration_id=self.supersedes_registration_id,
            metadata=self.metadata,
        )
        return RegisteredSecretProvider(
            registration_id=registration_id,
            workspace_id=self.workspace_id,
            provider_id=self.provider_id,
            provider_kind=self.provider_kind,
            display_name=self.display_name,
            endpoint_reference=self.endpoint_reference,
            credential_reference=self.credential_reference,
            allowed_reference_prefixes=self.allowed_reference_prefixes,
            allowed_intents=self.allowed_intents,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
            supersedes_registration_id=self.supersedes_registration_id,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class RegisterSecretReferenceCommand:
    workspace_id: str
    reference: SecretReference
    provider_registration_id: str
    allowed_intents: tuple[SecretUseIntent, ...]
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]
    supersedes_registration_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        self.candidate()

    def candidate(self) -> RegisteredSecretReference:
        registration_id = secret_reference_registration_id_for(
            workspace_id=self.workspace_id,
            reference=self.reference,
            provider_registration_id=self.provider_registration_id,
            allowed_intents=self.allowed_intents,
            supersedes_registration_id=self.supersedes_registration_id,
            metadata=self.metadata,
        )
        return RegisteredSecretReference(
            registration_id=registration_id,
            workspace_id=self.workspace_id,
            reference=self.reference,
            provider_registration_id=self.provider_registration_id,
            allowed_intents=self.allowed_intents,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
            supersedes_registration_id=self.supersedes_registration_id,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class RevokeSecretProviderCommand:
    workspace_id: str
    provider_id: SecretProviderId
    revoked_by: str
    revoked_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.provider_id, SecretProviderId):
            raise SecretProviderRegistrationError(
                "provider revocation requires SecretProviderId"
            )
        _require_identifier(self.revoked_by, "revoked_by")
        _require_bounded_text(self.revoked_at, "revoked_at", maximum=128)
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


@dataclass(frozen=True)
class RevokeSecretReferenceCommand:
    workspace_id: str
    registration_id: str
    revoked_by: str
    revoked_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.workspace_id, "workspace_id")
        _require_identifier(self.registration_id, "registration_id")
        _require_identifier(self.revoked_by, "revoked_by")
        _require_bounded_text(self.revoked_at, "revoked_at", maximum=128)
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


class SecretProviderRegistrationService:
    """Own provider/reference admission transaction boundaries."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register_provider(
        self,
        command: RegisterSecretProviderCommand,
    ) -> RegisteredSecretProvider:
        _require_command(command, RegisterSecretProviderCommand)
        _require_scope(command.actor_scopes, PolicyScope.SECRET_PROVIDER_REGISTER)
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.secret_providers.register(
                command.candidate()
            )
            unit_of_work.commit()
            return registered

    def revoke_provider(
        self,
        command: RevokeSecretProviderCommand,
    ) -> RegisteredSecretProvider:
        _require_command(command, RevokeSecretProviderCommand)
        _require_scope(command.actor_scopes, PolicyScope.SECRET_PROVIDER_REVOKE)
        with self._unit_of_work_factory() as unit_of_work:
            revoked = unit_of_work.stores.secret_providers.revoke_active(
                command.workspace_id,
                command.provider_id,
                revoked_by=command.revoked_by,
                revoked_at=command.revoked_at,
            )
            unit_of_work.commit()
            return revoked

    def register_reference(
        self,
        command: RegisterSecretReferenceCommand,
    ) -> RegisteredSecretReference:
        _require_command(command, RegisterSecretReferenceCommand)
        _require_scope(command.actor_scopes, PolicyScope.SECRET_PROVIDER_REGISTER)
        with self._unit_of_work_factory() as unit_of_work:
            provider = unit_of_work.stores.secret_providers.require_active_registration(
                command.workspace_id,
                command.provider_registration_id,
            )
            candidate = command.candidate()
            _validate_reference_admission(candidate, provider)
            registered = unit_of_work.stores.secret_references.register(candidate)
            unit_of_work.commit()
            return registered

    def revoke_reference(
        self,
        command: RevokeSecretReferenceCommand,
    ) -> RegisteredSecretReference:
        _require_command(command, RevokeSecretReferenceCommand)
        _require_scope(command.actor_scopes, PolicyScope.SECRET_PROVIDER_REVOKE)
        with self._unit_of_work_factory() as unit_of_work:
            revoked = unit_of_work.stores.secret_references.revoke(
                command.workspace_id,
                command.registration_id,
                revoked_by=command.revoked_by,
                revoked_at=command.revoked_at,
            )
            unit_of_work.commit()
            return revoked


def secret_provider_registration_id_for(
    *,
    workspace_id: str,
    provider_id: SecretProviderId,
    provider_kind: SecretProviderKind,
    display_name: str,
    endpoint_reference: SecretProviderEndpointReference,
    credential_reference: SecretReference,
    allowed_reference_prefixes: tuple[SecretReference, ...],
    allowed_intents: tuple[SecretUseIntent, ...],
    supersedes_registration_id: str | None,
    metadata: Mapping[str, object],
) -> str:
    provisional = RegisteredSecretProvider(
        registration_id="sprov_candidate",
        workspace_id=workspace_id,
        provider_id=provider_id,
        provider_kind=provider_kind,
        display_name=display_name,
        endpoint_reference=endpoint_reference,
        credential_reference=credential_reference,
        allowed_reference_prefixes=allowed_reference_prefixes,
        allowed_intents=allowed_intents,
        admitted_by="candidate",
        admitted_at="candidate",
        supersedes_registration_id=supersedes_registration_id,
        metadata=metadata,
    )
    digest = _digest(_provider_semantics(provisional))
    return f"sprov_{digest}"


def secret_reference_registration_id_for(
    *,
    workspace_id: str,
    reference: SecretReference,
    provider_registration_id: str,
    allowed_intents: tuple[SecretUseIntent, ...],
    supersedes_registration_id: str | None,
    metadata: Mapping[str, object],
) -> str:
    provisional = RegisteredSecretReference(
        registration_id="sref_candidate",
        workspace_id=workspace_id,
        reference=reference,
        provider_registration_id=provider_registration_id,
        allowed_intents=allowed_intents,
        admitted_by="candidate",
        admitted_at="candidate",
        supersedes_registration_id=supersedes_registration_id,
        metadata=metadata,
    )
    digest = _digest(_reference_semantics(provisional))
    return f"sref_{digest}"


def _validate_reference_admission(
    candidate: RegisteredSecretReference,
    provider: RegisteredSecretProvider,
) -> None:
    if candidate.workspace_id != provider.workspace_id:
        raise SecretProviderRegistrationError(
            "secret reference provider must belong to the same workspace"
        )
    if candidate.reference.provider_id != provider.provider_id:
        raise SecretProviderRegistrationError(
            "secret reference provider identity does not match registration"
        )
    if not set(candidate.allowed_intents).issubset(provider.allowed_intents):
        raise SecretProviderRegistrationError(
            "secret reference intents exceed provider admission"
        )
    if not any(
        _reference_is_within(candidate.reference, prefix)
        for prefix in provider.allowed_reference_prefixes
    ):
        raise SecretProviderRegistrationError(
            "secret reference is outside provider admission prefixes"
        )


def _reference_is_within(
    reference: SecretReference,
    prefix: SecretReference,
) -> bool:
    return (
        reference.provider_id == prefix.provider_id
        and reference.path[: len(prefix.path)] == prefix.path
    )


def _provider_semantics(provider: RegisteredSecretProvider) -> dict[str, object]:
    return {
        "workspace_id": provider.workspace_id,
        "provider_id": provider.provider_id.value,
        "provider_kind": provider.provider_kind.value,
        "display_name": provider.display_name,
        "endpoint_reference": provider.endpoint_reference.reference_id,
        "credential_reference": provider.credential_reference.reference_id,
        "allowed_reference_prefixes": [
            reference.reference_id
            for reference in provider.allowed_reference_prefixes
        ],
        "allowed_intents": [intent.value for intent in provider.allowed_intents],
        "supersedes_registration_id": provider.supersedes_registration_id,
        "metadata": dict(provider.metadata),
    }


def _reference_semantics(reference: RegisteredSecretReference) -> dict[str, object]:
    return {
        "workspace_id": reference.workspace_id,
        "reference_id": reference.reference.reference_id,
        "provider_registration_id": reference.provider_registration_id,
        "allowed_intents": [intent.value for intent in reference.allowed_intents],
        "supersedes_registration_id": reference.supersedes_registration_id,
        "metadata": dict(reference.metadata),
    }


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _reference_prefixes(
    values: tuple[SecretReference, ...],
    provider_id: SecretProviderId,
) -> tuple[SecretReference, ...]:
    if not isinstance(values, tuple) or not values:
        raise SecretProviderRegistrationError(
            "allowed_reference_prefixes must be a nonempty tuple"
        )
    if not all(isinstance(value, SecretReference) for value in values):
        raise SecretProviderRegistrationError(
            "allowed_reference_prefixes must contain SecretReference"
        )
    ordered = tuple(sorted(set(values), key=lambda item: item.reference_id))
    if any(value.provider_id != provider_id for value in ordered):
        raise SecretProviderRegistrationError(
            "provider prefix identity does not match provider_id"
        )
    return ordered


def _intents(values: tuple[SecretUseIntent, ...]) -> tuple[SecretUseIntent, ...]:
    if not isinstance(values, tuple) or not values:
        raise SecretProviderRegistrationError(
            "allowed_intents must be a nonempty tuple"
        )
    if not all(isinstance(value, SecretUseIntent) for value in values):
        raise SecretProviderRegistrationError(
            "allowed_intents must contain SecretUseIntent"
        )
    return tuple(sorted(set(values), key=lambda item: item.value))


def _metadata(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise SecretProviderRegistrationError(
            "metadata must be a bounded mapping"
        )
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _IDENTIFIER.fullmatch(key):
            raise SecretProviderRegistrationError("metadata key is malformed")
        normalized = key.lower().replace("-", "_")
        if normalized in _SECRET_SHAPED_METADATA_KEYS:
            raise SecretProviderRegistrationError(
                "metadata must not contain secret-bearing fields"
            )
        if not isinstance(item, (str, int, float, bool)) and item is not None:
            raise SecretProviderRegistrationError(
                "metadata values must be bounded scalars"
            )
        if isinstance(item, float) and not math.isfinite(item):
            raise SecretProviderRegistrationError(
                "metadata numeric values must be finite"
            )
        if isinstance(item, str):
            _require_bounded_text(item, f"metadata.{key}", maximum=512)
            lowered = item.lower()
            if any(marker in lowered for marker in _SECRET_VALUE_MARKERS):
                raise SecretProviderRegistrationError(
                    "metadata must not contain endpoint or secret material"
                )
        result[key] = item
    return result


def _require_command(command: object, expected: type[object]) -> None:
    if not isinstance(command, expected):
        raise SecretProviderRegistrationError(
            f"command must be {expected.__name__}"
        )


def _require_scope(
    scopes: tuple[PolicyScope, ...],
    required: PolicyScope,
) -> None:
    if required not in scopes:
        raise SecretProviderAuthorizationDenied(
            f"secret provider operation requires {required.value}"
        )


def _scopes(value: tuple[PolicyScope, ...]) -> tuple[PolicyScope, ...]:
    if not isinstance(value, tuple) or not all(
        isinstance(scope, PolicyScope) for scope in value
    ):
        raise SecretProviderRegistrationError(
            "actor_scopes must be a tuple of PolicyScope"
        )
    return tuple(dict.fromkeys(value))


def _require_secret_reference(value: object, field_name: str) -> None:
    if not isinstance(value, SecretReference):
        raise SecretProviderRegistrationError(
            f"{field_name} requires SecretReference"
        )
    if not _SECRET_REFERENCE_PREFIX.fullmatch(value.reference_id):
        raise SecretProviderRegistrationError(
            f"{field_name} is malformed"
        )


def _require_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SecretProviderRegistrationError(f"{field_name} is malformed")


def _require_bounded_text(
    value: object,
    field_name: str,
    *,
    maximum: int,
) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise SecretProviderRegistrationError(
            f"{field_name} must be nonempty bounded text"
        )


def _require_public_text(
    value: object,
    field_name: str,
    *,
    maximum: int,
) -> None:
    _require_bounded_text(value, field_name, maximum=maximum)
    lowered = str(value).lower()
    if any(marker in lowered for marker in _SECRET_VALUE_MARKERS):
        raise SecretProviderRegistrationError(
            f"{field_name} must not contain endpoint or secret material"
        )


def _validate_revocation_evidence(
    revoked: bool,
    revoked_by: str | None,
    revoked_at: str | None,
) -> None:
    if revoked:
        _require_identifier(revoked_by, "revoked_by")
        _require_bounded_text(revoked_at, "revoked_at", maximum=128)
        return
    if revoked_by is not None or revoked_at is not None:
        raise SecretProviderRegistrationError(
            "revocation evidence requires revoked status"
        )
