"""Safe custody intent, immutable receipt and atomic new-reference admission."""

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from control_plane_kit_core.identity import PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretCustodyGrant, SecretCustodyReceipt, SecretCustodyStatus,
    SecretProviderEndpointReference, SecretReference, SecretUseIntent,
)
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.effect_permissions import CurrentWorkspaceEffectPolicy, _standalone_context_matches
from control_plane_kit_operations.secret_providers import (
    SecretProviderNotFound, SecretProviderRegistrationError,
    generated_secret_reference_candidate, secret_custody_grant_for,
)


COMMAND_KIND = "secret-material.provision"
MAX_DESCRIPTOR_BYTES = 16384
SUPPORTED_INTENTS = frozenset((SecretUseIntent.CLOUDFLARE_API_TOKEN, SecretUseIntent.OCI_PULL_CREDENTIAL))
REQUIRED_SCOPES = frozenset((PolicyScope.SECRET_MATERIAL_PROVISION, PolicyScope.SECRET_PROVIDER_REGISTER))
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class ProvisionAttemptError(ValueError):
    """Bounded malformed intent, receipt or stored record."""


class ProvisionAttemptConflict(ProvisionAttemptError):
    """A request already exists or a transition contradicts recorded truth."""


class ProvisionAttemptNotFound(ProvisionAttemptError):
    """Exact absence, never malformed stored evidence."""


class ProvisionAttemptAuthorizationDenied(ProvisionAttemptError):
    """Current original-operator authority is missing."""


class ProvisionAttemptState(StrEnum):
    INTENDED = "intended"
    DISPATCHING = "dispatching"
    UNCERTAIN = "uncertain"
    RECEIPT_RECORDED = "receipt-recorded"
    ADMITTED = "admitted"
    REFUSED = "refused"
    FAILED_DEFINITE = "failed-definite"


def _identifier(value):
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ProvisionAttemptError("provision attempt identifier is malformed")


def _bounded_descriptor(value):
    valid = False
    if type(value) is dict:
        try:
            valid = len(json.dumps(value, ensure_ascii=True).encode("ascii")) <= MAX_DESCRIPTOR_BYTES
        except (TypeError, ValueError, RecursionError):
            pass
    if not valid:
        raise ProvisionAttemptError("provision attempt descriptor is malformed")


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode("ascii")).hexdigest()


def provision_attempt_id_for(workspace_id, request_id):
    _identifier(workspace_id)
    _identifier(request_id)
    return "smat_" + _hash({"workspace_id": workspace_id, "kind": COMMAND_KIND, "request_id": request_id})


@dataclass(frozen=True)
class ProvisionAttemptIntent:
    request_id: str
    actor: PrincipalIdentity
    grant: SecretCustodyGrant
    requested_at: str
    fingerprint: str = field(init=False)

    def __post_init__(self):
        _identifier(self.request_id)
        if type(self.actor) is not PrincipalIdentity or type(self.grant) is not SecretCustodyGrant:
            raise ProvisionAttemptError("provision intent requires typed actor and custody grant")
        valid_issuer = False
        try:
            valid_issuer = 0 < len(self.actor.issuer.encode("utf-8")) <= 512
        except (AttributeError, UnicodeEncodeError):
            pass
        if (not valid_issuer or self.actor.kind is not PrincipalKind.OPERATOR
                or self.actor.subject_id != self.grant.actor_subject):
            raise ProvisionAttemptError("provision intent requires the original operator")
        _identifier(self.actor.subject_id)
        if self.grant.intent not in SUPPORTED_INTENTS:
            raise ProvisionAttemptError("provision intent is unsupported")
        if self.grant.correlation_id != self.attempt_id:
            raise ProvisionAttemptError("provision correlation does not match request identity")
        validate_canonical_utc_timestamp(self.requested_at)
        data = self.descriptor()
        _bounded_descriptor(data)
        data.pop("requested_at")
        object.__setattr__(self, "fingerprint", _hash(data))

    @property
    def workspace_id(self):
        return self.grant.workspace_id

    @property
    def attempt_id(self):
        return provision_attempt_id_for(self.workspace_id, self.request_id)

    def descriptor(self):
        return {"kind": COMMAND_KIND, "request_id": self.request_id,
                "actor": self.actor.descriptor(), "custody_grant": self.grant.descriptor(),
                "requested_at": self.requested_at}


@dataclass(frozen=True)
class ProvisionAttemptClaim:
    """One initial claim. There is no later generation, reclaim or replay claim."""
    workspace_id: str
    request_id: str

    def __post_init__(self):
        _identifier(self.workspace_id)
        _identifier(self.request_id)


def validated_receipt(intent, receipt):
    valid = False
    if type(receipt) is SecretCustodyReceipt:
        try:
            # Reconstruct the closed value instead of retaining arbitrary provider objects.
            receipt = SecretCustodyReceipt(**{
                "custody_id": receipt.custody_id, "provider_registration_id": receipt.provider_registration_id,
                "reference": SecretReference(receipt.reference.reference_id), "version_id": receipt.version_id,
                "version_number": receipt.version_number, "status": receipt.status,
            })
            _bounded_descriptor(receipt.descriptor())
            valid = receipt.matches(intent.grant) and receipt.status is SecretCustodyStatus.ACTIVE
        except (ValueError, TypeError, AttributeError):
            pass
    if not valid:
        raise ProvisionAttemptError("provision receipt does not match exact active custody")
    return receipt


@dataclass(frozen=True)
class SecretMaterialProvisionAttempt:
    intent: ProvisionAttemptIntent
    state: ProvisionAttemptState
    receipt: SecretCustodyReceipt | None
    reference_registration_id: str | None
    created_at: str
    updated_at: str

    def __post_init__(self):
        if type(self.intent) is not ProvisionAttemptIntent or type(self.state) is not ProvisionAttemptState:
            raise ProvisionAttemptError("provision attempt is malformed")
        validate_canonical_utc_timestamp(self.created_at)
        validate_canonical_utc_timestamp(self.updated_at)
        if self.created_at != self.intent.requested_at:
            raise ProvisionAttemptError("provision creation does not match intent")
        has_receipt = self.state in (ProvisionAttemptState.RECEIPT_RECORDED, ProvisionAttemptState.ADMITTED)
        if has_receipt != (self.receipt is not None):
            raise ProvisionAttemptError("provision receipt state is malformed")
        if has_receipt:
            validated_receipt(self.intent, self.receipt)
        if self.state is ProvisionAttemptState.ADMITTED:
            _identifier(self.reference_registration_id)
        elif self.reference_registration_id is not None:
            raise ProvisionAttemptError("provision admission is premature")


class ProvisionAttemptAdmissionService:
    """Fold a retained receipt under current authority; performs no external IO.

    The command owner must obtain authoritative current scopes immediately before
    calling, outside this transaction. This owner checks identity/scopes and locks
    current provider truth; it does not infer permission freshness from a tuple.
    """
    def __init__(self, unit_of_work_factory: Any, *, permission_policy: CurrentWorkspaceEffectPolicy | None = None):
        self._uow = unit_of_work_factory
        if permission_policy is not None and type(permission_policy) is not CurrentWorkspaceEffectPolicy:
            raise ProvisionAttemptAuthorizationDenied("provision permission policy is invalid")
        self._permission_policy = permission_policy

    def admit(self, claim, *, actor, actor_scopes, admitted_at, command_context=None):
        if (type(actor) is not PrincipalIdentity or actor.kind is not PrincipalKind.OPERATOR
                or type(actor_scopes) is not tuple
                or not all(type(scope) is PolicyScope for scope in actor_scopes)
                or not REQUIRED_SCOPES.issubset(actor_scopes)):
            raise ProvisionAttemptAuthorizationDenied("provision admission requires current operator authority")
        validate_canonical_utc_timestamp(admitted_at)
        with self._uow() as uow:
            store = uow.stores.secret_material_provision_attempts
            attempt = store.require_claim(claim)
            if actor != attempt.intent.actor:
                raise ProvisionAttemptAuthorizationDenied("provision admission requires original operator")
            if attempt.state is not ProvisionAttemptState.RECEIPT_RECORDED:
                raise ProvisionAttemptConflict("provision has no pending receipt")
            if self._permission_policy is not None:
                if not _standalone_context_matches(command_context, actor, attempt.intent.workspace_id,
                        actor_scopes=actor_scopes):
                    raise ProvisionAttemptAuthorizationDenied("provision invocation binding is unavailable")
                decision = self._permission_policy.evaluate_in_unit_of_work(uow, command_context,
                    required_scopes=tuple(sorted(REQUIRED_SCOPES, key=lambda scope: scope.value)))
                if not decision.allowed:
                    raise ProvisionAttemptAuthorizationDenied("current provision admission is denied")
                actor_scopes = decision.effective_scopes
            grant = attempt.intent.grant
            current = None
            try:
                provider = uow.stores.secret_providers.require_active_registration_for_admission(
                    grant.workspace_id, grant.provider_registration_id)
                current = secret_custody_grant_for(
                    provider=provider, workspace_id=grant.workspace_id, reference=grant.reference,
                    intent=grant.intent, actor_subject=actor.subject_id, actor_scopes=actor_scopes,
                    correlation_id=grant.correlation_id, required_scope=PolicyScope.SECRET_MATERIAL_PROVISION,
                    operation_id=grant.operation_id, session_id=grant.session_id, run_id=grant.run_id,
                    activity_id=grant.activity_id, effect_id=grant.effect_id)
            except (SecretProviderNotFound, SecretProviderRegistrationError):
                pass
            if current != grant:
                raise ProvisionAttemptConflict("provision provider authority changed before admission")
            candidate = generated_secret_reference_candidate(
                grant=grant, receipt=validated_receipt(attempt.intent, attempt.receipt), admitted_at=admitted_at)
            reference = uow.stores.secret_references.register_new(candidate)
            result = store.record_admitted(claim, reference.registration_id, admitted_at=admitted_at)
            uow.commit()
        return result


@dataclass(frozen=True)
class ProvisionedSecretMaterialReceipt:
    """Public-only exact status. Custody evidence alone is not an admitted handle."""
    attempt_id: str
    workspace_id: str
    request_id: str
    state: ProvisionAttemptState
    correlation_id: str
    created_at: str
    updated_at: str
    version_id: str | None
    version_number: int | None
    reference_id: str | None
    reference_registration_id: str | None

    @classmethod
    def from_attempt(cls, attempt):
        if type(attempt) is not SecretMaterialProvisionAttempt:
            raise ProvisionAttemptError("provision status requires a typed attempt")
        intent = attempt.intent
        return cls(intent.attempt_id, intent.workspace_id, intent.request_id, attempt.state,
                   intent.grant.correlation_id, attempt.created_at, attempt.updated_at,
                   None if attempt.receipt is None else attempt.receipt.version_id,
                   None if attempt.receipt is None else attempt.receipt.version_number,
                   intent.grant.reference.reference_id if attempt.state is ProvisionAttemptState.ADMITTED else None,
                   attempt.reference_registration_id)

    def descriptor(self):
        return {"attempt_id": self.attempt_id, "workspace_id": self.workspace_id,
                "request_id": self.request_id, "state": self.state.value,
                "correlation_id": self.correlation_id, "created_at": self.created_at,
                "updated_at": self.updated_at,
                "custody": None if self.version_id is None else {
                    "version_id": self.version_id, "version_number": self.version_number},
                "admission": None if self.reference_id is None else {
                    "reference_id": self.reference_id, "reference_registration_id": self.reference_registration_id}}


def _closed(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ProvisionAttemptError("provision descriptor is malformed")
    return value


def decode_intent(value):
    _bounded_descriptor(value)
    result = None
    try:
        data = _closed(value, ("kind", "request_id", "actor", "custody_grant", "requested_at"))
        if data["kind"] != COMMAND_KIND:
            raise ValueError
        actor = _closed(data["actor"], ("issuer", "subject_id", "kind"))
        grant = dict(_closed(data["custody_grant"], (
            "custody_id", "workspace_id", "provider_registration_id", "endpoint_reference",
            "credential_reference", "reference_id", "intent", "actor_subject", "correlation_id",
            "custody_fingerprint", "operation_id", "session_id", "run_id", "activity_id", "effect_id")))
        grant["endpoint_reference"] = SecretProviderEndpointReference(grant["endpoint_reference"])
        grant["credential_reference"] = SecretReference(grant["credential_reference"])
        grant["reference"] = SecretReference(grant.pop("reference_id"))
        grant["intent"] = SecretUseIntent(grant["intent"])
        result = ProvisionAttemptIntent(data["request_id"], PrincipalIdentity(
            actor["issuer"], actor["subject_id"], PrincipalKind(actor["kind"])),
            SecretCustodyGrant(**grant), data["requested_at"])
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    if result is None:
        raise ProvisionAttemptError("stored provision intent is malformed")
    return result


def decode_receipt(value):
    _bounded_descriptor(value)
    result = None
    try:
        data = dict(_closed(value, ("custody_id", "provider_registration_id", "reference_id",
                                   "version_id", "version_number", "status")))
        data["reference"] = SecretReference(data.pop("reference_id"))
        data["status"] = SecretCustodyStatus(data["status"])
        result = SecretCustodyReceipt(**data)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    if result is None:
        raise ProvisionAttemptError("stored provision receipt is malformed")
    return result
