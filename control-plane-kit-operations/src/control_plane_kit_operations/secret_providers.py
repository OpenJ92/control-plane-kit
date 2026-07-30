"""Durable provider-neutral secret admission for operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretCustodyGrant,
    SecretCustodyReceipt,
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_CORRELATION_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
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


class SecretUseAuthorizationConflict(SecretProviderRegistrationConflict):
    """Raised when one correlation id is reused for different secret use."""


class SecretUseResolutionAuthorizer(Protocol):
    """Commit one exact use and return its reference-only interpreter grant."""

    def authorize_resolution(
        self,
        command: "AuthorizeSecretUse",
    ) -> SecretResolutionGrant: ...


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


@dataclass(frozen=True)
class AuthorizeSecretUse:
    """Request one exact, workspace-admitted secret use before provider IO."""

    workspace_id: str
    reference: SecretReference
    intent: SecretUseIntent
    actor_subject: str
    correlation_id: str
    requested_at: str
    actor_scopes: tuple[PolicyScope, ...]
    operation_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    activity_id: str | None = None
    effect_id: str | None = None
    probe_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.workspace_id, "workspace_id")
        _require_secret_reference(self.reference, "reference")
        if not isinstance(self.intent, SecretUseIntent):
            raise SecretProviderRegistrationError(
                "secret use requires SecretUseIntent"
            )
        _require_identifier(self.actor_subject, "actor_subject")
        _require_correlation_identifier(
            self.correlation_id,
            "correlation_id",
        )
        _require_bounded_text(self.requested_at, "requested_at", maximum=128)
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        for field_name in (
            "operation_id",
            "session_id",
            "run_id",
            "activity_id",
            "effect_id",
            "probe_id",
        ):
            _require_optional_correlation_identifier(
                getattr(self, field_name),
                field_name,
            )


@dataclass(frozen=True)
class AuthorizedSecretUse:
    """Durable operations evidence for one bounded use, never secret material."""

    authorization_id: str
    workspace_id: str
    reference_registration_id: str
    provider_registration_id: str
    reference: SecretReference
    intent: SecretUseIntent
    actor_subject: str
    correlation_id: str
    requested_at: str
    intent_fingerprint: str
    operation_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    activity_id: str | None = None
    effect_id: str | None = None
    probe_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.authorization_id, "authorization_id")
        _require_identifier(self.workspace_id, "workspace_id")
        _require_identifier(
            self.reference_registration_id,
            "reference_registration_id",
        )
        _require_identifier(
            self.provider_registration_id,
            "provider_registration_id",
        )
        _require_secret_reference(self.reference, "reference")
        if not isinstance(self.intent, SecretUseIntent):
            raise SecretProviderRegistrationError(
                "authorized use requires SecretUseIntent"
            )
        _require_identifier(self.actor_subject, "actor_subject")
        _require_correlation_identifier(
            self.correlation_id,
            "correlation_id",
        )
        _require_bounded_text(self.requested_at, "requested_at", maximum=128)
        if (
            not isinstance(self.intent_fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.intent_fingerprint)
        ):
            raise SecretProviderRegistrationError(
                "intent_fingerprint must be sha256"
            )
        for field_name in (
            "operation_id",
            "session_id",
            "run_id",
            "activity_id",
            "effect_id",
            "probe_id",
        ):
            _require_optional_correlation_identifier(
                getattr(self, field_name),
                field_name,
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "authorization_id": self.authorization_id,
            "workspace_id": self.workspace_id,
            "reference_registration_id": self.reference_registration_id,
            "provider_registration_id": self.provider_registration_id,
            "reference_id": self.reference.reference_id,
            "intent": self.intent.value,
            "actor_subject": self.actor_subject,
            "correlation_id": self.correlation_id,
            "requested_at": self.requested_at,
            "operation_id": self.operation_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "activity_id": self.activity_id,
            "effect_id": self.effect_id,
            "probe_id": self.probe_id,
        }


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


class SecretUseAuthorizationService:
    """Authorize one admitted use without crossing the provider IO boundary."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def authorize(self, command: AuthorizeSecretUse) -> AuthorizedSecretUse:
        _require_command(command, AuthorizeSecretUse)
        _require_scope(command.actor_scopes, PolicyScope.SECRET_PROVIDER_USE)
        with self._unit_of_work_factory() as unit_of_work:
            authorized, _ = _authorize_secret_use(
                unit_of_work,
                command,
            )
            unit_of_work.commit()
            return authorized

    def authorize_resolution(
        self,
        command: AuthorizeSecretUse,
    ) -> SecretResolutionGrant:
        """Commit one exact use and return only its pinned IO routing references."""

        _require_command(command, AuthorizeSecretUse)
        _require_scope(command.actor_scopes, PolicyScope.SECRET_PROVIDER_USE)
        with self._unit_of_work_factory() as unit_of_work:
            authorized, provider = _authorize_secret_use(
                unit_of_work,
                command,
            )
            grant = secret_resolution_grant_for(
                authorized,
                provider=provider,
            )
            unit_of_work.commit()
            return grant


def _authorize_secret_use(
    unit_of_work: Any,
    command: AuthorizeSecretUse,
) -> tuple[AuthorizedSecretUse, RegisteredSecretProvider]:
    store = unit_of_work.stores.secret_use_authorizations
    store.lock_correlation(
        command.workspace_id,
        command.correlation_id,
    )
    reference = unit_of_work.stores.secret_references.get_active(
        command.workspace_id,
        command.reference,
    )
    provider = unit_of_work.stores.secret_providers.require_active_registration(
        command.workspace_id,
        reference.provider_registration_id,
    )
    _validate_reference_admission(reference, provider)
    if command.intent not in reference.allowed_intents:
        raise SecretProviderRegistrationError(
            "secret use intent is outside reference admission"
        )

    candidate = authorized_secret_use_for(
        command,
        reference=reference,
        provider=provider,
    )
    existing = store.for_correlation(
        command.workspace_id,
        command.correlation_id,
    )
    if existing is not None:
        if existing.intent_fingerprint != candidate.intent_fingerprint:
            raise SecretUseAuthorizationConflict(
                "secret use correlation was reused with different intent"
            )
        return existing, provider

    store.add(candidate)
    return candidate, provider


def authorized_secret_use_for(
    command: AuthorizeSecretUse,
    *,
    reference: RegisteredSecretReference,
    provider: RegisteredSecretProvider,
) -> AuthorizedSecretUse:
    """Build deterministic evidence from current admitted operational truth."""

    _require_command(command, AuthorizeSecretUse)
    semantics = {
        "workspace_id": command.workspace_id,
        "reference_registration_id": reference.registration_id,
        "provider_registration_id": provider.registration_id,
        "reference_id": command.reference.reference_id,
        "intent": command.intent.value,
        "actor_subject": command.actor_subject,
        "correlation_id": command.correlation_id,
        "operation_id": command.operation_id,
        "session_id": command.session_id,
        "run_id": command.run_id,
        "activity_id": command.activity_id,
        "effect_id": command.effect_id,
        "probe_id": command.probe_id,
    }
    fingerprint = _digest(semantics)
    return AuthorizedSecretUse(
        authorization_id=f"suse_{fingerprint}",
        workspace_id=command.workspace_id,
        reference_registration_id=reference.registration_id,
        provider_registration_id=provider.registration_id,
        reference=command.reference,
        intent=command.intent,
        actor_subject=command.actor_subject,
        correlation_id=command.correlation_id,
        requested_at=command.requested_at,
        intent_fingerprint=fingerprint,
        operation_id=command.operation_id,
        session_id=command.session_id,
        run_id=command.run_id,
        activity_id=command.activity_id,
        effect_id=command.effect_id,
        probe_id=command.probe_id,
    )


def secret_resolution_grant_for(
    authorized: AuthorizedSecretUse,
    *,
    provider: RegisteredSecretProvider,
) -> SecretResolutionGrant:
    """Project committed operations evidence into the pure interpreter grant."""

    if not isinstance(authorized, AuthorizedSecretUse):
        raise SecretProviderRegistrationError(
            "secret resolution grant requires AuthorizedSecretUse"
        )
    if not isinstance(provider, RegisteredSecretProvider):
        raise SecretProviderRegistrationError(
            "secret resolution grant requires RegisteredSecretProvider"
        )
    if (
        authorized.workspace_id != provider.workspace_id
        or authorized.provider_registration_id != provider.registration_id
    ):
        raise SecretProviderRegistrationError(
            "secret resolution grant provider identity does not match authorization"
        )
    return SecretResolutionGrant(
        authorization_id=authorized.authorization_id,
        workspace_id=authorized.workspace_id,
        reference_registration_id=authorized.reference_registration_id,
        provider_registration_id=authorized.provider_registration_id,
        endpoint_reference=provider.endpoint_reference,
        credential_reference=provider.credential_reference,
        reference=authorized.reference,
        intent=authorized.intent,
        actor_subject=authorized.actor_subject,
        correlation_id=authorized.correlation_id,
        intent_fingerprint=authorized.intent_fingerprint,
        operation_id=authorized.operation_id,
        session_id=authorized.session_id,
        run_id=authorized.run_id,
        activity_id=authorized.activity_id,
        effect_id=authorized.effect_id,
        probe_id=authorized.probe_id,
    )


def secret_custody_grant_for(
    *,
    provider: RegisteredSecretProvider,
    workspace_id: str,
    reference: SecretReference,
    intent: SecretUseIntent,
    actor_subject: str,
    actor_scopes: tuple[PolicyScope, ...],
    correlation_id: str,
    operation_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    activity_id: str | None = None,
    effect_id: str | None = None,
) -> SecretCustodyGrant:
    """Authorize one deterministic generated reference under admitted provider truth."""

    if not isinstance(provider, RegisteredSecretProvider):
        raise SecretProviderRegistrationError(
            "secret custody requires RegisteredSecretProvider"
        )
    _require_scope(actor_scopes, PolicyScope.SECRET_PROVIDER_USE)
    _require_identifier(workspace_id, "workspace_id")
    _require_secret_reference(reference, "reference")
    if not isinstance(intent, SecretUseIntent):
        raise SecretProviderRegistrationError(
            "secret custody requires SecretUseIntent"
        )
    _require_identifier(actor_subject, "actor_subject")
    _require_correlation_identifier(correlation_id, "correlation_id")
    if provider.workspace_id != workspace_id:
        raise SecretProviderRegistrationError(
            "secret custody provider belongs to a different workspace"
        )
    candidate = RegisteredSecretReference(
        registration_id="sref_candidate",
        workspace_id=workspace_id,
        reference=reference,
        provider_registration_id=provider.registration_id,
        allowed_intents=(intent,),
        admitted_by=actor_subject,
        admitted_at="candidate",
    )
    _validate_reference_admission(candidate, provider)
    semantics = {
        "workspace_id": workspace_id,
        "provider_registration_id": provider.registration_id,
        "reference_id": reference.reference_id,
        "intent": intent.value,
        "actor_subject": actor_subject,
        "correlation_id": correlation_id,
        "operation_id": operation_id,
        "session_id": session_id,
        "run_id": run_id,
        "activity_id": activity_id,
        "effect_id": effect_id,
    }
    fingerprint = _digest(semantics)
    custody_identity = _digest(
        {
            "workspace_id": workspace_id,
            "provider_registration_id": provider.registration_id,
            "reference_id": reference.reference_id,
            "intent": intent.value,
        }
    )
    return SecretCustodyGrant(
        custody_id=f"scust_{custody_identity}",
        workspace_id=workspace_id,
        provider_registration_id=provider.registration_id,
        endpoint_reference=provider.endpoint_reference,
        credential_reference=provider.credential_reference,
        reference=reference,
        intent=intent,
        actor_subject=actor_subject,
        correlation_id=correlation_id,
        custody_fingerprint=fingerprint,
        operation_id=operation_id,
        session_id=session_id,
        run_id=run_id,
        activity_id=activity_id,
        effect_id=effect_id,
    )


def generated_secret_reference_candidate(
    *,
    grant: SecretCustodyGrant,
    receipt: SecretCustodyReceipt,
    admitted_at: str,
) -> RegisteredSecretReference:
    """Build operations admission from an exact provider custody receipt."""

    if not isinstance(grant, SecretCustodyGrant) or not isinstance(
        receipt,
        SecretCustodyReceipt,
    ):
        raise SecretProviderRegistrationError(
            "generated secret admission requires custody grant and receipt"
        )
    if not receipt.matches(grant):
        raise SecretProviderRegistrationError(
            "secret custody receipt does not match its grant"
        )
    metadata = {
        "custody_id": receipt.custody_id,
        "provider_version_id": receipt.version_id,
        "provider_version_number": receipt.version_number,
    }
    return RegisterSecretReferenceCommand(
        workspace_id=grant.workspace_id,
        reference=grant.reference,
        provider_registration_id=grant.provider_registration_id,
        allowed_intents=(grant.intent,),
        admitted_by=grant.actor_subject,
        admitted_at=admitted_at,
        actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
        metadata=metadata,
    ).candidate()


def secret_custody_correlation_for(
    *,
    workspace_id: str,
    provider_registration_id: str,
    reference: SecretReference,
    intent: SecretUseIntent,
    actor_subject: str,
    operation_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    activity_id: str | None = None,
    effect_id: str | None = None,
) -> str:
    """Derive retry-stable correlation for one generated-secret custody write."""

    _require_identifier(workspace_id, "workspace_id")
    _require_identifier(
        provider_registration_id,
        "provider_registration_id",
    )
    _require_secret_reference(reference, "reference")
    if not isinstance(intent, SecretUseIntent):
        raise SecretProviderRegistrationError(
            "secret custody correlation requires SecretUseIntent"
        )
    _require_identifier(actor_subject, "actor_subject")
    semantics = {
        "workspace_id": workspace_id,
        "provider_registration_id": provider_registration_id,
        "reference_id": reference.reference_id,
        "intent": intent.value,
        "actor_subject": actor_subject,
        "operation_id": operation_id,
        "session_id": session_id,
        "run_id": run_id,
        "activity_id": activity_id,
        "effect_id": effect_id,
    }
    return f"secret-custody:{_digest(semantics)}"


def secret_use_correlation_for(
    *,
    workspace_id: str,
    reference: SecretReference,
    intent: SecretUseIntent,
    actor_subject: str,
    operation_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    activity_id: str | None = None,
    effect_id: str | None = None,
    probe_id: str | None = None,
) -> str:
    """Derive retry-stable correlation for one exact secret use."""

    _require_identifier(workspace_id, "workspace_id")
    _require_secret_reference(reference, "reference")
    if not isinstance(intent, SecretUseIntent):
        raise SecretProviderRegistrationError(
            "secret use correlation requires SecretUseIntent"
        )
    _require_identifier(actor_subject, "actor_subject")
    semantics = {
        "workspace_id": workspace_id,
        "reference_id": reference.reference_id,
        "intent": intent.value,
        "actor_subject": actor_subject,
        "operation_id": operation_id,
        "session_id": session_id,
        "run_id": run_id,
        "activity_id": activity_id,
        "effect_id": effect_id,
        "probe_id": probe_id,
    }
    return f"secret-use:{_digest(semantics)}"


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


def _require_correlation_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _CORRELATION_IDENTIFIER.fullmatch(value):
        raise SecretProviderRegistrationError(f"{field_name} is malformed")


def _require_optional_correlation_identifier(
    value: object,
    field_name: str,
) -> None:
    if value is not None:
        _require_correlation_identifier(value, field_name)


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
