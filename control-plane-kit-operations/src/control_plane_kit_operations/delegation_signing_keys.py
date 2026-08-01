"""Durable secret-free delegation signing-key registration and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any

from control_plane_kit_core.delegation_keys import (
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_operations.secret_providers import SecretProviderNotFound


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


class DelegationSigningKeyError(ValueError):
    """Base error for delegation signing-key lifecycle commands."""


class DelegationSigningKeyConflict(DelegationSigningKeyError):
    """Raised when immutable identity or lifecycle evidence conflicts."""


class DelegationSigningKeyNotFound(DelegationSigningKeyError):
    """Raised when a workspace key identity cannot be selected."""


class DelegationSigningKeyAuthorizationDenied(DelegationSigningKeyError):
    """Raised when an actor lacks one focused delegation-key scope."""


class RegisteredDelegationSigningKeyStatus(StrEnum):
    """Durable signing and verification lifecycle."""

    VERIFY_ONLY = "verify-only"
    ACTIVE = "active"
    RETIRED = "retired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class RegisteredDelegationSigningKey:
    """One immutable key identity plus mutable lifecycle evidence."""

    registration_id: str
    workspace_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    public_key: DelegationPublicKey
    private_key_reference: SecretReference
    admitted_by: str
    admitted_at: str
    status: RegisteredDelegationSigningKeyStatus = (
        RegisteredDelegationSigningKeyStatus.VERIFY_ONLY
    )
    activated_by: str | None = None
    activated_at: str | None = None
    retired_by: str | None = None
    retired_at: str | None = None
    revoked_by: str | None = None
    revoked_at: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.registration_id, "registration_id")
        _identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise DelegationSigningKeyError("delegation key purpose is unsupported")
        _identifier(self.issuer, "issuer")
        if not isinstance(self.public_key, DelegationPublicKey):
            raise DelegationSigningKeyError(
                "delegation key requires public verification material"
            )
        if not isinstance(self.private_key_reference, SecretReference):
            raise DelegationSigningKeyError(
                "delegation key requires a private SecretReference"
            )
        _identifier(self.admitted_by, "admitted_by")
        _text(self.admitted_at, "admitted_at")
        if not isinstance(self.status, RegisteredDelegationSigningKeyStatus):
            raise DelegationSigningKeyError("delegation key status is unsupported")
        _paired(self.activated_by, self.activated_at, "activation")
        _paired(self.retired_by, self.retired_at, "retirement")
        _paired(self.revoked_by, self.revoked_at, "revocation")
        if self.status is RegisteredDelegationSigningKeyStatus.ACTIVE:
            if self.activated_by is None:
                raise DelegationSigningKeyError("active key requires activation evidence")
        if self.status is RegisteredDelegationSigningKeyStatus.RETIRED:
            if self.retired_by is None:
                raise DelegationSigningKeyError("retired key requires retirement evidence")
        if self.status is RegisteredDelegationSigningKeyStatus.REVOKED:
            if self.revoked_by is None:
                raise DelegationSigningKeyError("revoked key requires revocation evidence")

    @property
    def key_id(self) -> str:
        return self.public_key.key_id

    def descriptor(self) -> dict[str, object]:
        return {
            "registration_id": self.registration_id,
            "workspace_id": self.workspace_id,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
            **self.public_key.descriptor(),
            "private_key_reference": self.private_key_reference.reference_id,
            "admitted_by": self.admitted_by,
            "admitted_at": self.admitted_at,
            "status": self.status.value,
            "activated_by": self.activated_by,
            "activated_at": self.activated_at,
            "retired_by": self.retired_by,
            "retired_at": self.retired_at,
            "revoked_by": self.revoked_by,
            "revoked_at": self.revoked_at,
        }

    def same_identity_as(self, other: "RegisteredDelegationSigningKey") -> bool:
        return (
            self.workspace_id,
            self.purpose,
            self.issuer,
            self.key_id,
            self.public_key.algorithm,
            self.public_key.public_key_pem,
            self.private_key_reference,
        ) == (
            other.workspace_id,
            other.purpose,
            other.issuer,
            other.key_id,
            other.public_key.algorithm,
            other.public_key.public_key_pem,
            other.private_key_reference,
        )


@dataclass(frozen=True)
class RegisterDelegationSigningKeyCommand:
    workspace_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    public_key: DelegationPublicKey
    private_key_reference: SecretReference
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def candidate(self) -> RegisteredDelegationSigningKey:
        registration_id = delegation_signing_key_registration_id_for(
            workspace_id=self.workspace_id,
            purpose=self.purpose,
            issuer=self.issuer,
            public_key=self.public_key,
            private_key_reference=self.private_key_reference,
        )
        return RegisteredDelegationSigningKey(
            registration_id=registration_id,
            workspace_id=self.workspace_id,
            purpose=self.purpose,
            issuer=self.issuer,
            public_key=self.public_key,
            private_key_reference=self.private_key_reference,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
        )


@dataclass(frozen=True)
class ActivateDelegationSigningKeyCommand:
    workspace_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str
    activated_by: str
    activated_at: str
    actor_scopes: tuple[PolicyScope, ...]


@dataclass(frozen=True)
class RetireDelegationSigningKeyCommand:
    workspace_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str
    retired_by: str
    retired_at: str
    actor_scopes: tuple[PolicyScope, ...]


@dataclass(frozen=True)
class RevokeDelegationSigningKeyCommand:
    workspace_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str
    revoked_by: str
    revoked_at: str
    actor_scopes: tuple[PolicyScope, ...]


class DelegationSigningKeyRegistrationService:
    """Own one explicit transaction for each lifecycle command."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register(
        self,
        command: RegisterDelegationSigningKeyCommand,
    ) -> RegisteredDelegationSigningKey:
        _command(command, RegisterDelegationSigningKeyCommand)
        _scope(command.actor_scopes, PolicyScope.DELEGATION_KEY_REGISTER)
        candidate = command.candidate()
        with self._unit_of_work_factory() as unit_of_work:
            try:
                reference = unit_of_work.stores.secret_references.get_active(
                    candidate.workspace_id,
                    candidate.private_key_reference,
                )
            except SecretProviderNotFound as error:
                raise DelegationSigningKeyConflict(
                    "private key reference is not actively admitted"
                ) from error
            if SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY not in reference.allowed_intents:
                raise DelegationSigningKeyConflict(
                    "private key reference is not admitted for delegation signing"
                )
            registered = unit_of_work.stores.delegation_signing_keys.register(
                candidate
            )
            unit_of_work.commit()
            return registered

    def activate(
        self,
        command: ActivateDelegationSigningKeyCommand,
    ) -> RegisteredDelegationSigningKey:
        _lifecycle_command(command, ActivateDelegationSigningKeyCommand)
        _scope(command.actor_scopes, PolicyScope.DELEGATION_KEY_ACTIVATE)
        with self._unit_of_work_factory() as unit_of_work:
            candidate = unit_of_work.stores.delegation_signing_keys.get(
                command.workspace_id,
                command.purpose,
                command.issuer,
                command.key_id,
            )
            try:
                reference = unit_of_work.stores.secret_references.get_active(
                    command.workspace_id,
                    candidate.private_key_reference,
                )
            except SecretProviderNotFound as error:
                raise DelegationSigningKeyConflict(
                    "private key reference is not actively admitted"
                ) from error
            if SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY not in reference.allowed_intents:
                raise DelegationSigningKeyConflict(
                    "private key reference is not admitted for delegation signing"
                )
            active = unit_of_work.stores.delegation_signing_keys.activate(
                command.workspace_id,
                command.purpose,
                command.issuer,
                command.key_id,
                activated_by=command.activated_by,
                activated_at=command.activated_at,
            )
            unit_of_work.commit()
            return active

    def retire(
        self,
        command: RetireDelegationSigningKeyCommand,
    ) -> RegisteredDelegationSigningKey:
        _lifecycle_command(command, RetireDelegationSigningKeyCommand)
        _scope(command.actor_scopes, PolicyScope.DELEGATION_KEY_RETIRE)
        with self._unit_of_work_factory() as unit_of_work:
            retired = unit_of_work.stores.delegation_signing_keys.retire(
                command.workspace_id,
                command.purpose,
                command.issuer,
                command.key_id,
                retired_by=command.retired_by,
                retired_at=command.retired_at,
            )
            unit_of_work.commit()
            return retired

    def revoke(
        self,
        command: RevokeDelegationSigningKeyCommand,
    ) -> RegisteredDelegationSigningKey:
        _lifecycle_command(command, RevokeDelegationSigningKeyCommand)
        _scope(command.actor_scopes, PolicyScope.DELEGATION_KEY_REVOKE)
        with self._unit_of_work_factory() as unit_of_work:
            revoked = unit_of_work.stores.delegation_signing_keys.revoke(
                command.workspace_id,
                command.purpose,
                command.issuer,
                command.key_id,
                revoked_by=command.revoked_by,
                revoked_at=command.revoked_at,
            )
            unit_of_work.commit()
            return revoked


def delegation_signing_key_registration_id_for(
    *,
    workspace_id: str,
    purpose: DelegationKeyPurpose,
    issuer: str,
    public_key: DelegationPublicKey,
    private_key_reference: SecretReference,
) -> str:
    document = {
        "workspace_id": workspace_id,
        "purpose": purpose.value,
        "issuer": issuer,
        "key_id": public_key.key_id,
        "algorithm": public_key.algorithm.value,
        "fingerprint_sha256": public_key.fingerprint_sha256,
        "private_key_reference": private_key_reference.reference_id,
    }
    digest = sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"dkey_{digest}"


def _scope(scopes: tuple[PolicyScope, ...], required: PolicyScope) -> None:
    if required not in scopes:
        raise DelegationSigningKeyAuthorizationDenied(
            f"delegation signing key command requires {required.value}"
        )


def _command(value: object, expected: type[object]) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"command must be {expected.__name__}")


def _lifecycle_command(value: object, expected: type[object]) -> None:
    _command(value, expected)
    _identifier(getattr(value, "workspace_id"), "workspace_id")
    if not isinstance(getattr(value, "purpose"), DelegationKeyPurpose):
        raise DelegationSigningKeyError("delegation key purpose is unsupported")
    _identifier(getattr(value, "issuer"), "issuer")
    _identifier(getattr(value, "key_id"), "key_id")


def _identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DelegationSigningKeyError(f"{field_name} is malformed")


def _text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise DelegationSigningKeyError(f"{field_name} is malformed")


def _paired(actor: str | None, at: str | None, label: str) -> None:
    if (actor is None) != (at is None):
        raise DelegationSigningKeyError(f"{label} evidence must be complete")
    if actor is not None:
        _identifier(actor, f"{label}_by")
        _text(at, f"{label}_at")


__all__ = [
    "ActivateDelegationSigningKeyCommand",
    "DelegationSigningKeyAuthorizationDenied",
    "DelegationSigningKeyConflict",
    "DelegationSigningKeyError",
    "DelegationSigningKeyNotFound",
    "DelegationSigningKeyRegistrationService",
    "RegisterDelegationSigningKeyCommand",
    "RegisteredDelegationSigningKey",
    "RegisteredDelegationSigningKeyStatus",
    "RetireDelegationSigningKeyCommand",
    "RevokeDelegationSigningKeyCommand",
    "delegation_signing_key_registration_id_for",
]
