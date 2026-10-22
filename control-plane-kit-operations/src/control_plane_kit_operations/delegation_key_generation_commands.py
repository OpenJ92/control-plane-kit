"""Explicit provider generation between committed durable owner transitions."""

from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.identity import PrincipalAuthorizer, PrincipalKind, TrustedCommandContext
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.delegation_key_generation import (
    DelegationKeyGenerationConflict, DelegationKeyGenerationError,
    DelegationKeyGenerationEvidence, DelegationKeyGenerationService,
    DelegationKeyGenerator, GenerateDelegationSigningKey,
)
from control_plane_kit_operations.delegation_key_generation_attempts import (
    GeneratedDelegationKeyReceipt, GenerationAttemptAdmissionService,
    GenerationAttemptAuthorizationDenied, GenerationAttemptConflict,
    GenerationAttemptError, GenerationAttemptIntent, GenerationAttemptNotFound,
    GenerationAttemptState, _identifier, generation_attempt_id_for, validated_receipt,
)
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyConflict
from control_plane_kit_operations.secret_providers import (
    SecretProviderRegistrationError,
)


PUBLIC_GENERATION_PURPOSES = frozenset((
    DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
    DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
    DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
))
_REQUIRED_SCOPES = frozenset((PolicyScope.DELEGATION_KEY_GENERATE, PolicyScope.DELEGATION_KEY_REGISTER))
_TERMINAL = frozenset((GenerationAttemptState.ADMITTED, GenerationAttemptState.REFUSED,
                       GenerationAttemptState.FAILED_DEFINITE))


class GenerationCommandUnavailable(GenerationAttemptError):
    """Bounded infrastructure failure; no claim of persisted outcome or retry."""


def _context(value):
    if type(value) is not TrustedCommandContext:
        raise GenerationAttemptAuthorizationDenied("generation requires trusted workspace authority")
    _identifier(value.workspace_id)
    if value.principal.identity.kind is not PrincipalKind.OPERATOR:
        raise GenerationAttemptAuthorizationDenied("generation requires an authenticated operator")
    if not _REQUIRED_SCOPES.issubset(value.granted_scopes):
        raise GenerationAttemptAuthorizationDenied("generation requires generate and register authority")


@dataclass(frozen=True)
class GenerateDelegationKeyCommand:
    context: TrustedCommandContext
    request_id: str
    provider_registration_id: str
    secret_reference: SecretReference
    purpose: DelegationKeyPurpose
    issuer: str

    def __post_init__(self):
        _context(self.context)
        for value in (self.request_id, self.provider_registration_id, self.issuer):
            _identifier(value)
        if type(self.secret_reference) is not SecretReference:
            raise GenerationAttemptError("generation reference is malformed")
        if type(self.purpose) is not DelegationKeyPurpose or self.purpose not in PUBLIC_GENERATION_PURPOSES:
            raise GenerationAttemptError("generation purpose is not public")


@dataclass(frozen=True)
class ReconcileDelegationKeyGenerationCommand:
    context: TrustedCommandContext
    request_id: str

    def __post_init__(self):
        _context(self.context)
        _identifier(self.request_id)


class DelegationKeyGenerationCommandService:
    """Compose attempt ownership without retaining credentials or provider clients.

    The required authorizer MUST consult authoritative current permission/denial
    evidence on each call. Returning immutable request grants without consulting
    that authority is not a valid implementation. Unavailable evidence must deny.
    Operations enforces the original identity/workspace and request-grant ceiling;
    Servers composition must name and prove the actual authority adapter. This
    port does not promise token/session revocation beyond its identity contract.
    """

    def __init__(self, unit_of_work_factory: Any, generator: DelegationKeyGenerator,
                 *, authorizer: PrincipalAuthorizer, clock: Callable[[], str]):
        self._uow = unit_of_work_factory
        self._generator = generator
        self._authorizer = authorizer
        self._clock = clock
        self._generation = DelegationKeyGenerationService(unit_of_work_factory)
        self._admission = GenerationAttemptAdmissionService(unit_of_work_factory)

    def generate(self, command: GenerateDelegationKeyCommand) -> GeneratedDelegationKeyReceipt:
        if type(command) is not GenerateDelegationKeyCommand:
            raise GenerationAttemptError("generation command is malformed")
        return self._bounded(lambda: self._generate(command))

    def reconcile(self, command: ReconcileDelegationKeyGenerationCommand) -> GeneratedDelegationKeyReceipt:
        if type(command) is not ReconcileDelegationKeyGenerationCommand:
            raise GenerationAttemptError("generation reconciliation command is malformed")
        return self._bounded(lambda: self._reconcile(command))

    def _bounded(self, operation):
        try:
            return GeneratedDelegationKeyReceipt.from_attempt(operation())
        except GenerationAttemptError:
            raise
        except Exception:
            result = None
        raise GenerationCommandUnavailable("generation outcome could not be durably completed")

    def _now(self):
        result = self._clock()
        validate_canonical_utc_timestamp(result)
        return result

    def _authorize(self, original):
        _context(original)
        current = None
        try:
            current = self._authorizer.authorize(original.principal, original.workspace_id)
            _context(current)
            valid = (current.workspace_id == original.workspace_id
                     and current.principal.identity == original.principal.identity
                     and set(current.granted_scopes).issubset(original.granted_scopes))
        except Exception:
            valid = False
        if not valid:
            raise GenerationAttemptAuthorizationDenied("current generation authority is unavailable or denied")
        return current

    def _read(self, workspace_id, request_id):
        with self._uow() as uow:
            return uow.stores.delegation_key_generation_attempts.get(workspace_id, request_id)

    def _generate(self, command):
        current = self._authorize(command.context)
        existing = None
        try:
            existing = self._read(current.workspace_id, command.request_id)
        except GenerationAttemptNotFound:
            existing = None
        if existing is not None:
            grant = existing.intent.grant
            if (existing.intent.actor != current.principal.identity
                    or (grant.provider_registration_id, grant.reference, grant.purpose, grant.issuer)
                    != (command.provider_registration_id, command.secret_reference, command.purpose, command.issuer)):
                raise GenerationAttemptConflict("generation request conflicts with recorded intent")
            return existing
        grant = self._prepare(GenerateDelegationSigningKey(
            workspace_id=current.workspace_id, provider_registration_id=command.provider_registration_id,
            reference=command.secret_reference, purpose=command.purpose, issuer=command.issuer,
            actor_subject=current.actor_id,
            correlation_id=generation_attempt_id_for(current.workspace_id, command.request_id),
            requested_at=self._now(), actor_scopes=current.granted_scopes,
        ))
        self._require_new_destination(grant)
        intent = GenerationAttemptIntent(command.request_id, current.principal.identity, grant)
        with self._uow() as uow:
            attempt = uow.stores.delegation_key_generation_attempts.reserve(intent, created_at=self._now())
            uow.commit()
        return self._dispatch(command.context, attempt, reconcile=False)

    def _reconcile(self, command):
        current = self._authorize(command.context)
        attempt = self._read(current.workspace_id, command.request_id)
        if attempt.intent.actor != current.principal.identity:
            raise GenerationAttemptAuthorizationDenied("generation reconciliation requires original operator")
        if attempt.intent.grant.purpose not in PUBLIC_GENERATION_PURPOSES:
            raise GenerationAttemptError("generation purpose is not public")
        return self._dispatch(command.context, attempt, reconcile=True)

    def _prepare(self, command):
        result = None
        try:
            result = self._generation.prepare(command)
        except (DelegationKeyGenerationError, SecretProviderRegistrationError):
            result = None
        if result is None:
            raise GenerationAttemptConflict("generation provider authority is unavailable or changed")
        return result

    def _recheck_provider(self, intent, context):
        original = intent.grant
        current = self._prepare(GenerateDelegationSigningKey(
            workspace_id=original.workspace_id, provider_registration_id=original.provider_registration_id,
            reference=original.reference, purpose=original.purpose, issuer=original.issuer,
            actor_subject=context.actor_id, correlation_id=original.correlation_id,
            requested_at=original.requested_at, actor_scopes=context.granted_scopes,
        ))
        if current != original:
            raise GenerationAttemptConflict("generation provider binding changed")

    def _require_new_destination(self, grant):
        with self._uow() as uow:
            history = uow.stores.secret_references.list_history(grant.workspace_id, grant.reference)
        if history:
            raise GenerationAttemptConflict("generation destination is already admitted")

    def _dispatch(self, original_context, attempt, *, reconcile):
        if attempt.state in _TERMINAL or (not reconcile and attempt.state is not GenerationAttemptState.INTENDED):
            return attempt
        current = self._authorize(original_context)
        self._recheck_provider(attempt.intent, current)
        if attempt.receipt is None:
            self._require_new_destination(attempt.intent.grant)
        with self._uow() as uow:
            store = uow.stores.delegation_key_generation_attempts
            claim = (store.claim_reconciliation if reconcile else store.claim_initial)(
                attempt.intent.workspace_id, attempt.intent.request_id,
                expected_fence=attempt.fence, claimed_at=self._now(),
            )
            uow.commit()
        if claim is None:
            return self._read(attempt.intent.workspace_id, attempt.intent.request_id)
        if attempt.receipt is None:
            # Claim commit can take time. Recheck after it, immediately before
            # invocation, without holding a transaction across either port.
            current = self._authorize(original_context)
            self._recheck_provider(attempt.intent, current)
            self._require_new_destination(attempt.intent.grant)
            evidence = None
            try:
                result = self._generator.generate(attempt.intent.grant)
                evidence = validated_receipt(attempt.intent,
                    DelegationKeyGenerationEvidence.from_provider_result(attempt.intent.grant, result))
            except Exception:
                evidence = None
            # Persist only typed evidence or bounded uncertainty, outside the
            # provider exception handler so no raw cause enters public errors.
            try:
                with self._uow() as uow:
                    store = uow.stores.delegation_key_generation_attempts
                    if evidence is None:
                        attempt = store.record_uncertain(claim, observed_at=self._now())
                    else:
                        attempt = store.record_receipt(claim, evidence, observed_at=self._now())
                    uow.commit()
            except GenerationAttemptConflict:
                return self._read(claim.workspace_id, claim.request_id)
            if evidence is None:
                return attempt
        current = self._authorize(original_context)
        try:
            self._recheck_provider(attempt.intent, current)
            return self._admission.admit(claim, actor=current.principal.identity,
                                         actor_scopes=current.granted_scopes, admitted_at=self._now())
        except (GenerationAttemptConflict, DelegationKeyGenerationConflict,
                DelegationSigningKeyConflict, SecretProviderRegistrationError):
            # Provider receipt survives refusal; explicit reconciliation is the
            # only subsequent mutation. No provider failure or admission claim.
            return self._read(claim.workspace_id, claim.request_id)
