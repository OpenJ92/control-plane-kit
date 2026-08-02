"""Provider-neutral delegation-key generation and durable result folding."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol

from control_plane_kit_core.delegation_keys import (
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretCustodyGrant,
    SecretCustodyReceipt,
    SecretCustodyStatus,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisterDelegationSigningKeyCommand,
    RegisteredDelegationSigningKey,
)
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretReference,
    SecretProviderNotFound,
    generated_secret_reference_candidate,
    secret_custody_grant_for,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class DelegationKeyGenerationError(ValueError):
    """Base bounded failure for delegation-key generation coordination."""


class DelegationKeyGenerationConflict(DelegationKeyGenerationError):
    """Raised when provider evidence does not match admitted generation intent."""


class DelegationKeyGenerationAuthorizationDenied(DelegationKeyGenerationError):
    """Raised when generation or registration authority is absent."""


@dataclass(frozen=True)
class GenerateDelegationSigningKey:
    """Operator intent to generate one private key under admitted custody."""

    workspace_id: str
    provider_registration_id: str
    reference: SecretReference
    purpose: DelegationKeyPurpose
    issuer: str
    actor_subject: str
    correlation_id: str
    requested_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _identifier(self.workspace_id, "workspace_id")
        _identifier(self.provider_registration_id, "provider_registration_id")
        if not isinstance(self.reference, SecretReference):
            raise DelegationKeyGenerationError("generation reference is malformed")
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise DelegationKeyGenerationError("generation purpose is unsupported")
        _identifier(self.issuer, "issuer")
        _identifier(self.actor_subject, "actor_subject")
        _identifier(self.correlation_id, "correlation_id")
        _text(self.requested_at, "requested_at")
        _scopes(self.actor_scopes)


@dataclass(frozen=True)
class DelegationKeyGenerationGrant:
    """Reference-only authority derived from committed provider truth."""

    custody_grant: SecretCustodyGrant
    purpose: DelegationKeyPurpose
    issuer: str
    requested_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.custody_grant, SecretCustodyGrant):
            raise DelegationKeyGenerationError(
                "generation grant requires secret custody authority"
            )
        if self.custody_grant.intent is not SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY:
            raise DelegationKeyGenerationError(
                "generation grant has an unsupported secret intent"
            )
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise DelegationKeyGenerationError("generation purpose is unsupported")
        _identifier(self.issuer, "issuer")
        _text(self.requested_at, "requested_at")

    @property
    def workspace_id(self) -> str:
        return self.custody_grant.workspace_id

    @property
    def provider_registration_id(self) -> str:
        return self.custody_grant.provider_registration_id

    @property
    def reference(self) -> SecretReference:
        return self.custody_grant.reference

    @property
    def actor_subject(self) -> str:
        return self.custody_grant.actor_subject

    @property
    def correlation_id(self) -> str:
        return self.custody_grant.correlation_id


class DelegationKeyGenerationProviderVersion(Protocol):
    version_id: str
    version_number: int


class DelegationKeyGenerationProviderResult(Protocol):
    reference: SecretReference
    metadata: DelegationKeyGenerationProviderVersion
    purpose: DelegationKeyPurpose
    issuer: str
    correlation_id: str
    public_key: DelegationPublicKey
    replayed: bool


class DelegationKeyGenerator(Protocol):
    """External implementation supplied at the cpk-server composition boundary."""

    def generate(
        self,
        grant: DelegationKeyGenerationGrant,
    ) -> DelegationKeyGenerationProviderResult: ...


@dataclass(frozen=True)
class DelegationKeyGenerationEvidence:
    """Secret-free evidence returned by a provider-side generation effect."""

    workspace_id: str
    reference: SecretReference
    purpose: DelegationKeyPurpose
    issuer: str
    correlation_id: str
    version_id: str
    version_number: int
    public_key: DelegationPublicKey
    replayed: bool

    def __post_init__(self) -> None:
        _identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.reference, SecretReference):
            raise DelegationKeyGenerationError("generation evidence is malformed")
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise DelegationKeyGenerationError("generation purpose is unsupported")
        _identifier(self.issuer, "issuer")
        _identifier(self.correlation_id, "correlation_id")
        _identifier(self.version_id, "version_id")
        if type(self.version_number) is not int or self.version_number < 1:
            raise DelegationKeyGenerationError(
                "generation version number is malformed"
            )
        if not isinstance(self.public_key, DelegationPublicKey):
            raise DelegationKeyGenerationError(
                "generation evidence requires public key material"
            )
        if type(self.replayed) is not bool:
            raise DelegationKeyGenerationError("generation replay flag is malformed")

    @classmethod
    def from_provider_result(
        cls,
        grant: DelegationKeyGenerationGrant,
        result: object,
    ) -> "DelegationKeyGenerationEvidence":
        """Validate a structural interpreter result without importing its package."""

        if not isinstance(grant, DelegationKeyGenerationGrant):
            raise DelegationKeyGenerationError(
                "provider result requires generation grant"
            )
        try:
            metadata = getattr(result, "metadata")
            return cls(
                workspace_id=grant.workspace_id,
                reference=getattr(result, "reference"),
                purpose=getattr(result, "purpose"),
                issuer=getattr(result, "issuer"),
                correlation_id=getattr(result, "correlation_id"),
                version_id=getattr(metadata, "version_id"),
                version_number=getattr(metadata, "version_number"),
                public_key=getattr(result, "public_key"),
                replayed=getattr(result, "replayed"),
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise DelegationKeyGenerationConflict(
                "provider returned malformed delegation-key evidence"
            ) from error


@dataclass(frozen=True)
class AdmitGeneratedDelegationSigningKey:
    grant: DelegationKeyGenerationGrant
    evidence: DelegationKeyGenerationEvidence
    admitted_by: str
    admitted_at: str
    actor_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.grant, DelegationKeyGenerationGrant):
            raise DelegationKeyGenerationError("admission grant is malformed")
        if not isinstance(self.evidence, DelegationKeyGenerationEvidence):
            raise DelegationKeyGenerationError("admission evidence is malformed")
        _identifier(self.admitted_by, "admitted_by")
        _text(self.admitted_at, "admitted_at")
        _scopes(self.actor_scopes)


@dataclass(frozen=True)
class AdmittedGeneratedDelegationSigningKey:
    secret_reference: RegisteredSecretReference
    signing_key: RegisteredDelegationSigningKey
    provider_version_id: str
    provider_version_number: int
    replayed: bool


class DelegationKeyGenerationService:
    """Prepare and fold generation around, never across, provider IO."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def prepare(
        self,
        command: GenerateDelegationSigningKey,
    ) -> DelegationKeyGenerationGrant:
        if not isinstance(command, GenerateDelegationSigningKey):
            raise TypeError("command must be GenerateDelegationSigningKey")
        _scope(command.actor_scopes, PolicyScope.DELEGATION_KEY_GENERATE)
        with self._unit_of_work_factory() as unit_of_work:
            try:
                provider = unit_of_work.stores.secret_providers.require_active_registration(
                    command.workspace_id,
                    command.provider_registration_id,
                )
            except SecretProviderNotFound as error:
                raise DelegationKeyGenerationConflict(
                    "generation provider is not actively admitted"
                ) from error
            custody_grant = secret_custody_grant_for(
                provider=provider,
                workspace_id=command.workspace_id,
                reference=command.reference,
                intent=SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,
                actor_subject=command.actor_subject,
                actor_scopes=command.actor_scopes,
                correlation_id=command.correlation_id,
                required_scope=PolicyScope.DELEGATION_KEY_GENERATE,
            )
            grant = DelegationKeyGenerationGrant(
                custody_grant=custody_grant,
                purpose=command.purpose,
                issuer=command.issuer,
                requested_at=command.requested_at,
            )
            unit_of_work.commit()
            return grant

    def admit_generated(
        self,
        command: AdmitGeneratedDelegationSigningKey,
    ) -> AdmittedGeneratedDelegationSigningKey:
        if not isinstance(command, AdmitGeneratedDelegationSigningKey):
            raise TypeError(
                "command must be AdmitGeneratedDelegationSigningKey"
            )
        _scope(command.actor_scopes, PolicyScope.DELEGATION_KEY_REGISTER)
        _match(command.grant, command.evidence)
        receipt = SecretCustodyReceipt(
            custody_id=command.grant.custody_grant.custody_id,
            provider_registration_id=command.grant.provider_registration_id,
            reference=command.evidence.reference,
            version_id=command.evidence.version_id,
            version_number=command.evidence.version_number,
            status=SecretCustodyStatus.ACTIVE,
        )
        reference_candidate = generated_secret_reference_candidate(
            grant=command.grant.custody_grant,
            receipt=receipt,
            admitted_at=command.admitted_at,
        )
        signing_key_candidate = RegisterDelegationSigningKeyCommand(
            workspace_id=command.grant.workspace_id,
            purpose=command.grant.purpose,
            issuer=command.grant.issuer,
            public_key=command.evidence.public_key,
            private_key_reference=command.grant.reference,
            admitted_by=command.admitted_by,
            admitted_at=command.admitted_at,
            actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
        ).candidate()
        with self._unit_of_work_factory() as unit_of_work:
            try:
                provider = unit_of_work.stores.secret_providers.require_active_registration(
                    command.grant.workspace_id,
                    command.grant.provider_registration_id,
                )
            except SecretProviderNotFound as error:
                raise DelegationKeyGenerationConflict(
                    "generation provider changed before durable fold"
                ) from error
            if (
                provider.endpoint_reference
                != command.grant.custody_grant.endpoint_reference
                or provider.credential_reference
                != command.grant.custody_grant.credential_reference
            ):
                raise DelegationKeyGenerationConflict(
                    "generation provider changed before durable fold"
                )
            secret_reference = unit_of_work.stores.secret_references.register(
                reference_candidate
            )
            signing_key = unit_of_work.stores.delegation_signing_keys.register(
                signing_key_candidate
            )
            unit_of_work.commit()
        return AdmittedGeneratedDelegationSigningKey(
            secret_reference=secret_reference,
            signing_key=signing_key,
            provider_version_id=command.evidence.version_id,
            provider_version_number=command.evidence.version_number,
            replayed=command.evidence.replayed,
        )


def _match(
    grant: DelegationKeyGenerationGrant,
    evidence: DelegationKeyGenerationEvidence,
) -> None:
    if (
        evidence.workspace_id != grant.workspace_id
        or evidence.reference != grant.reference
        or evidence.purpose is not grant.purpose
        or evidence.issuer != grant.issuer
        or evidence.correlation_id != grant.correlation_id
    ):
        raise DelegationKeyGenerationConflict(
            "provider evidence does not match generation grant"
        )


def _scope(scopes: tuple[PolicyScope, ...], required: PolicyScope) -> None:
    _scopes(scopes)
    if required not in scopes:
        raise DelegationKeyGenerationAuthorizationDenied(
            f"delegation key generation requires {required.value}"
        )


def _scopes(scopes: tuple[PolicyScope, ...]) -> None:
    if (
        not isinstance(scopes, tuple)
        or not scopes
        or not all(isinstance(scope, PolicyScope) for scope in scopes)
    ):
        raise DelegationKeyGenerationError("actor scopes are malformed")


def _identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DelegationKeyGenerationError(f"{field_name} is malformed")


def _text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise DelegationKeyGenerationError(f"{field_name} is malformed")


__all__ = [
    "AdmitGeneratedDelegationSigningKey",
    "AdmittedGeneratedDelegationSigningKey",
    "DelegationKeyGenerationAuthorizationDenied",
    "DelegationKeyGenerationConflict",
    "DelegationKeyGenerationError",
    "DelegationKeyGenerationEvidence",
    "DelegationKeyGenerationGrant",
    "DelegationKeyGenerationProviderResult",
    "DelegationKeyGenerationProviderVersion",
    "DelegationKeyGenerationService",
    "DelegationKeyGenerator",
    "GenerateDelegationSigningKey",
]
