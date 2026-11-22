"""Typed durable generation intent and transaction-bound admission; no provider IO."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey,
)
from control_plane_kit_core.identity import PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretCustodyGrant, SecretProviderEndpointReference, SecretReference, SecretUseIntent,
)
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.effect_permissions import CurrentWorkspaceEffectPolicy, _standalone_context_matches
from control_plane_kit_operations.delegation_key_generation import (
    AdmitGeneratedDelegationSigningKey, DelegationKeyGenerationEvidence,
    DelegationKeyGenerationGrant, _match,
    admit_generated_in_unit_of_work,
)


COMMAND_KIND = "delegation-key.generate"
MAX_DESCRIPTOR_BYTES = 16384
MAX_FENCE = 2**63 - 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class GenerationAttemptError(ValueError):
    """Bounded malformed attempt or evidence failure."""


class GenerationAttemptConflict(GenerationAttemptError):
    """Immutable intent, claim or durable outcome conflicts."""


class GenerationAttemptNotFound(GenerationAttemptConflict):
    """The exact request has no durable record; corruption is not absence."""


class GenerationAttemptAuthorizationDenied(GenerationAttemptError):
    """Original operator identity or current admission authority is absent."""


class GenerationAttemptState(StrEnum):
    INTENDED = "intended"
    DISPATCHING = "dispatching"
    UNCERTAIN = "uncertain"
    RECEIPT_RECORDED = "receipt-recorded"
    ADMITTED = "admitted"
    REFUSED = "refused"
    FAILED_DEFINITE = "failed-definite"


class GenerationClaimKind(StrEnum):
    INITIAL = "initial"
    RECONCILE_PROVIDER = "reconcile-provider"
    RECONCILE_ADMISSION = "reconcile-admission"


def _identifier(value: object) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise GenerationAttemptError("generation attempt identifier is malformed")


def _bounded_text(value: object, limit: int) -> None:
    valid = False
    if type(value) is str:
        try:
            valid = bool(value.strip()) and len(value.encode("utf-8")) <= limit
        except UnicodeEncodeError:
            pass
    if not valid:
        raise GenerationAttemptError("generation attempt text is malformed")


def _bounded_descriptor(value: object) -> None:
    valid = False
    if type(value) is dict:
        try:
            valid = len(json.dumps(value, ensure_ascii=True).encode("ascii")) <= MAX_DESCRIPTOR_BYTES
        except (ValueError, TypeError, RecursionError):
            pass
    if not valid:
        raise GenerationAttemptError("generation attempt descriptor is malformed")


def _hash(value: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")).hexdigest()


def generation_attempt_id_for(workspace_id: str, request_id: str) -> str:
    """Derive the original provider correlation from the durable request identity."""
    _identifier(workspace_id)
    _identifier(request_id)
    return "gkat_" + _hash({
        "workspace_id": workspace_id, "kind": COMMAND_KIND, "request_id": request_id,
    })


@dataclass(frozen=True)
class GenerationAttemptIntent:
    request_id: str
    actor: PrincipalIdentity
    grant: DelegationKeyGenerationGrant
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _identifier(self.request_id)
        if type(self.actor) is not PrincipalIdentity or type(self.grant) is not DelegationKeyGenerationGrant:
            raise GenerationAttemptError("generation attempt intent is malformed")
        if type(self.grant.custody_grant) is not SecretCustodyGrant:
            raise GenerationAttemptError("generation attempt custody is malformed")
        _bounded_text(self.actor.issuer, 512)
        _identifier(self.actor.subject_id)
        if self.actor.subject_id != self.grant.actor_subject:
            raise GenerationAttemptError("generation attempt actor does not match grant")
        validate_canonical_utc_timestamp(self.grant.requested_at)
        descriptor = self.descriptor()
        _bounded_descriptor(descriptor)
        # Arrival times are server metadata, never duplicate-request identity.
        semantics = dict(descriptor)
        semantics.pop("requested_at")
        object.__setattr__(self, "fingerprint", _hash(semantics))

    @property
    def workspace_id(self) -> str:
        return self.grant.workspace_id

    @property
    def attempt_id(self) -> str:
        return generation_attempt_id_for(self.workspace_id, self.request_id)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": COMMAND_KIND, "request_id": self.request_id,
            "actor": self.actor.descriptor(),
            "custody_grant": self.grant.custody_grant.descriptor(),
            "purpose": self.grant.purpose.value, "issuer": self.grant.issuer,
            "requested_at": self.grant.requested_at,
        }


@dataclass(frozen=True)
class GenerationAttemptClaim:
    workspace_id: str
    request_id: str
    fence: int
    kind: GenerationClaimKind

    def __post_init__(self) -> None:
        _identifier(self.workspace_id)
        _identifier(self.request_id)
        if type(self.fence) is not int or not 1 <= self.fence <= MAX_FENCE or type(self.kind) is not GenerationClaimKind:
            raise GenerationAttemptError("generation attempt claim is malformed")


def validated_receipt(intent: GenerationAttemptIntent, evidence: object) -> DelegationKeyGenerationEvidence:
    valid = False
    if type(evidence) is DelegationKeyGenerationEvidence:
        try:
            _match(intent.grant, evidence)
            # Reconstruct existing public values, rather than retaining an arbitrary result body.
            public_key = DelegationPublicKey(
                evidence.public_key.key_id, evidence.public_key.algorithm, evidence.public_key.public_key_pem
            )
            evidence = replace(evidence, public_key=public_key)
            _bounded_descriptor(receipt_descriptor(evidence))
            valid = True
        except (ValueError, TypeError, AttributeError):
            pass
    if not valid:
        raise GenerationAttemptError("generation receipt does not match bounded public intent")
    return evidence


@dataclass(frozen=True)
class DelegationKeyGenerationAttempt:
    intent: GenerationAttemptIntent
    state: GenerationAttemptState
    fence: int
    claim_kind: GenerationClaimKind | None
    receipt: DelegationKeyGenerationEvidence | None
    reference_registration_id: str | None
    key_registration_id: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if type(self.intent) is not GenerationAttemptIntent or type(self.state) is not GenerationAttemptState:
            raise GenerationAttemptError("generation attempt record is malformed")
        if type(self.fence) is not int or not 0 <= self.fence <= MAX_FENCE:
            raise GenerationAttemptError("generation attempt fence is malformed")
        validate_canonical_utc_timestamp(self.created_at)
        validate_canonical_utc_timestamp(self.updated_at)
        intended = self.state is GenerationAttemptState.INTENDED
        if intended != (self.fence == 0 and self.claim_kind is None):
            raise GenerationAttemptError("generation attempt dispatch state is malformed")
        if not intended and (self.fence == 0 or type(self.claim_kind) is not GenerationClaimKind):
            raise GenerationAttemptError("generation attempt dispatch state is malformed")
        has_receipt = self.state in (GenerationAttemptState.RECEIPT_RECORDED, GenerationAttemptState.ADMITTED)
        if has_receipt != (self.receipt is not None):
            raise GenerationAttemptError("generation attempt receipt state is malformed")
        if self.receipt is not None:
            validated_receipt(self.intent, self.receipt)
        if self.claim_kind is GenerationClaimKind.RECONCILE_ADMISSION and not has_receipt:
            raise GenerationAttemptError("generation attempt admission claim requires receipt")
        if self.state is GenerationAttemptState.ADMITTED:
            _identifier(self.reference_registration_id)
            _identifier(self.key_registration_id)
        elif self.reference_registration_id is not None or self.key_registration_id is not None:
            raise GenerationAttemptError("generation attempt admission identities are premature")


class GenerationAttemptAdmissionService:
    """Register retained evidence and its attempt under local admission authority."""

    def __init__(self, unit_of_work_factory: Any, *, permission_policy: CurrentWorkspaceEffectPolicy | None = None) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        if permission_policy is not None and type(permission_policy) is not CurrentWorkspaceEffectPolicy:
            raise GenerationAttemptAuthorizationDenied("generation permission policy is invalid")
        self._permission_policy = permission_policy

    def admit(self, claim: GenerationAttemptClaim, *, actor: PrincipalIdentity,
              actor_scopes: tuple[PolicyScope, ...], admitted_at: str,
              command_context=None) -> DelegationKeyGenerationAttempt:
        if type(claim) is not GenerationAttemptClaim:
            raise GenerationAttemptError("generation attempt claim is malformed")
        if (type(actor) is not PrincipalIdentity or type(actor_scopes) is not tuple
                or not all(type(scope) is PolicyScope for scope in actor_scopes)
                or not {PolicyScope.DELEGATION_KEY_GENERATE, PolicyScope.DELEGATION_KEY_REGISTER}.issubset(actor_scopes)):
            raise GenerationAttemptAuthorizationDenied("generation attempt admission requires current authority")
        with self._unit_of_work_factory() as uow:
            store = uow.stores.delegation_key_generation_attempts
            attempt = store.require_claim(claim)
            if actor != attempt.intent.actor:
                raise GenerationAttemptAuthorizationDenied("generation attempt requires original operator")
            if attempt.state is not GenerationAttemptState.RECEIPT_RECORDED:
                raise GenerationAttemptConflict("generation attempt has no pending admission")
            if self._permission_policy is not None:
                if not _standalone_context_matches(command_context, actor, attempt.intent.workspace_id,
                        actor_scopes=actor_scopes):
                    raise GenerationAttemptAuthorizationDenied("generation invocation binding is unavailable")
                decision = self._permission_policy.evaluate_in_unit_of_work(uow, command_context,
                    required_scopes=(PolicyScope.DELEGATION_KEY_GENERATE, PolicyScope.DELEGATION_KEY_REGISTER))
                if not decision.allowed:
                    raise GenerationAttemptAuthorizationDenied("current generation admission is denied")
                actor_scopes = decision.effective_scopes
            admitted = admit_generated_in_unit_of_work(uow, AdmitGeneratedDelegationSigningKey(
                grant=attempt.intent.grant, evidence=attempt.receipt,
                admitted_by=actor.subject_id, admitted_at=admitted_at, actor_scopes=actor_scopes,
            ))
            result = store.record_admitted(claim, admitted, admitted_at=admitted_at)
            uow.commit()
        return result


@dataclass(frozen=True)
class GeneratedDelegationKeyReceipt:
    """Exact authorized request result; provider receipt is not CPK admission."""

    attempt_id: str
    workspace_id: str
    request_id: str
    state: GenerationAttemptState
    purpose: DelegationKeyPurpose
    issuer: str
    correlation_id: str
    created_at: str
    updated_at: str
    receipt: DelegationKeyGenerationEvidence | None
    reference_registration_id: str | None
    key_registration_id: str | None

    @classmethod
    def from_attempt(cls, attempt: DelegationKeyGenerationAttempt) -> "GeneratedDelegationKeyReceipt":
        if type(attempt) is not DelegationKeyGenerationAttempt:
            raise GenerationAttemptError("generation receipt requires a typed attempt")
        intent = attempt.intent
        return cls(
            attempt_id=intent.attempt_id, workspace_id=intent.workspace_id,
            request_id=intent.request_id, state=attempt.state,
            purpose=intent.grant.purpose, issuer=intent.grant.issuer,
            correlation_id=intent.grant.correlation_id,
            created_at=attempt.created_at, updated_at=attempt.updated_at,
            receipt=attempt.receipt,
            reference_registration_id=attempt.reference_registration_id,
            key_registration_id=attempt.key_registration_id,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "state": self.state.value,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "receipt": None if self.receipt is None else receipt_descriptor(self.receipt),
            "admission": None if self.state is not GenerationAttemptState.ADMITTED else {
                "reference_registration_id": self.reference_registration_id,
                "key_registration_id": self.key_registration_id,
            },
        }


def receipt_descriptor(evidence: DelegationKeyGenerationEvidence) -> dict[str, object]:
    return {
        "workspace_id": evidence.workspace_id, "reference": evidence.reference.reference_id,
        "purpose": evidence.purpose.value, "issuer": evidence.issuer,
        "correlation_id": evidence.correlation_id, "version_id": evidence.version_id,
        "version_number": evidence.version_number, "replayed": evidence.replayed,
        "public_key": {"key_id": evidence.public_key.key_id,
                       "algorithm": evidence.public_key.algorithm.value,
                       "public_key_pem": evidence.public_key.public_key_pem},
    }


def _closed(value: object, keys: tuple[str, ...]) -> dict:
    if type(value) is not dict or set(value) != set(keys):
        raise GenerationAttemptError("generation attempt descriptor is malformed")
    return value


def decode_intent(value: object) -> GenerationAttemptIntent:
    _bounded_descriptor(value)
    failed = False
    try:
        data = _closed(value, ("kind", "request_id", "actor", "custody_grant", "purpose", "issuer", "requested_at"))
        if data["kind"] != COMMAND_KIND:
            raise ValueError
        actor = _closed(data["actor"], ("issuer", "subject_id", "kind"))
        custody = dict(_closed(data["custody_grant"], (
            "custody_id", "workspace_id", "provider_registration_id", "endpoint_reference",
            "credential_reference", "reference_id", "intent", "actor_subject", "correlation_id",
            "custody_fingerprint", "operation_id", "session_id", "run_id", "activity_id", "effect_id",
        )))
        custody["endpoint_reference"] = SecretProviderEndpointReference(custody["endpoint_reference"])
        custody["credential_reference"] = SecretReference(custody["credential_reference"])
        custody["reference"] = SecretReference(custody.pop("reference_id"))
        custody["intent"] = SecretUseIntent(custody["intent"])
        result = GenerationAttemptIntent(data["request_id"], PrincipalIdentity(
            actor["issuer"], actor["subject_id"], PrincipalKind(actor["kind"])
        ), DelegationKeyGenerationGrant(
            SecretCustodyGrant(**custody), DelegationKeyPurpose(data["purpose"]),
            data["issuer"], data["requested_at"],
        ))
    except (ValueError, TypeError, KeyError, AttributeError):
        failed = True
    if failed:
        raise GenerationAttemptError("stored generation intent is malformed")
    return result


def decode_receipt(value: object) -> DelegationKeyGenerationEvidence:
    _bounded_descriptor(value)
    failed = False
    try:
        data = dict(_closed(value, ("workspace_id", "reference", "purpose", "issuer", "correlation_id",
                                   "version_id", "version_number", "replayed", "public_key")))
        key = _closed(data["public_key"], ("key_id", "algorithm", "public_key_pem"))
        data["public_key"] = DelegationPublicKey(key["key_id"], DelegationKeyAlgorithm(key["algorithm"]), key["public_key_pem"])
        data["reference"] = SecretReference(data["reference"])
        data["purpose"] = DelegationKeyPurpose(data["purpose"])
        result = DelegationKeyGenerationEvidence(**data)
    except (ValueError, TypeError, KeyError, AttributeError):
        failed = True
    if failed:
        raise GenerationAttemptError("stored generation receipt is malformed")
    return result


__all__ = [
    "DelegationKeyGenerationAttempt", "GenerationAttemptAdmissionService",
    "GenerationAttemptAuthorizationDenied", "GenerationAttemptClaim",
    "GenerationAttemptConflict", "GenerationAttemptError", "GenerationAttemptIntent",
    "GenerationAttemptState", "GenerationClaimKind",
]
