"""Current workspace permission inside an existing effect admission transaction."""

from dataclasses import dataclass
import re

from control_plane_kit_core.identity import IdentityContractError, PrincipalKind, TrustedCommandContext
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.managed_runtime_assignments import (
    ManagedRuntimeCapabilityPolicy, ManagedRuntimeSelection,
)
from control_plane_kit_operations.records import (
    ExecutionCommandReceiptRecord, ExecutionCommandReceiptStatus,
    ManagedExecutionCommandIntent, OperationsRecordError,
)
from control_plane_kit_operations.runtime_authorities import RuntimeAuthorityNotFound
from control_plane_kit_operations.workspace_permissions import current_workspace_context


class EffectPermissionDenied(ValueError):
    """A trusted owner binding or current effect permission is unavailable."""


@dataclass(frozen=True)
class ExecutionCommandReference:
    run_id: str
    idempotency_key: str
    intent_fingerprint: str

    def __post_init__(self):
        if (type(self.run_id) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", self.run_id) is None
                or type(self.idempotency_key) is not str
                or not 1 <= len(self.idempotency_key) <= 200
                or any(ord(character) < 32 for character in self.idempotency_key)
                or type(self.intent_fingerprint) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.intent_fingerprint) is None):
            raise EffectPermissionDenied("execution permission reference is invalid")


@dataclass(frozen=True)
class EffectCommandContext:
    original: TrustedCommandContext
    receipt: ExecutionCommandReference

    def __post_init__(self):
        if type(self.receipt) is not ExecutionCommandReference:
            raise EffectPermissionDenied("execution permission context is invalid")
        self.receipt.__post_init__()
        _original_intent(self.original)


@dataclass(frozen=True)
class CurrentEffectDecision:
    allowed: bool
    effective_scopes: tuple[PolicyScope, ...]
    permission_revision: int | None
    managed_selection: ManagedRuntimeSelection | None = None
    denial_code: str | None = None


@dataclass(frozen=True)
class CurrentWorkspaceEffectPolicy:
    installation_id: str
    capability_policy: ManagedRuntimeCapabilityPolicy

    def __post_init__(self):
        if (type(self.capability_policy) is not ManagedRuntimeCapabilityPolicy
                or self.installation_id != self.capability_policy.installation_id):
            raise EffectPermissionDenied("effect permission installation is invalid")
        self.capability_policy.__post_init__()

    def evaluate_in_unit_of_work(self, uow, original: TrustedCommandContext, *,
            required_scopes: tuple[PolicyScope, ...], managed_target=None) -> CurrentEffectDecision:
        """Caller owns prefix locks, its intent binding, admission write and commit."""
        _original_intent(original)
        if (type(required_scopes) is not tuple or not required_scopes
                or any(type(scope) is not PolicyScope for scope in required_scopes)):
            raise EffectPermissionDenied("effect permission scopes are invalid")
        record = uow.stores.workspace_permissions.get(self.installation_id,
            original.workspace_id, original.principal.identity, for_update=True)
        revision = None if record is None else record.revision
        try:
            effective = current_workspace_context(original.principal.identity,
                original.workspace_id, record, original.granted_scopes).granted_scopes
        except IdentityContractError:
            return CurrentEffectDecision(False, (), revision, denial_code="workspace-permission-denied")
        if any(scope not in effective for scope in required_scopes):
            return CurrentEffectDecision(False, effective, revision, denial_code="required-scope-denied")
        selected = None
        if managed_target is not None:
            if (type(managed_target) is not ManagedRuntimeSelection
                    or managed_target.installation_id != self.installation_id
                    or managed_target.workspace_id != original.workspace_id):
                return CurrentEffectDecision(False, effective, revision, denial_code="management-target-denied")
            try:
                authority = uow.stores.runtime_authorities.get_registration_for_update(
                    original.workspace_id, managed_target.authority_ref, managed_target.registration_id)
            except RuntimeAuthorityNotFound:
                return CurrentEffectDecision(False, effective, revision, denial_code="management-authority-denied")
            selected = uow.stores.managed_runtime_assignments.select(
                workspace_id=original.workspace_id, runtime_id=managed_target.runtime_id,
                authority=authority, capability_policy=self.capability_policy, expected=managed_target)
            if selected is None:
                return CurrentEffectDecision(False, effective, revision, denial_code="management-assignment-denied")
        return CurrentEffectDecision(True, effective, revision, selected)


def _original_intent(original):
    invalid = False
    try:
        return ManagedExecutionCommandIntent.from_context(original)
    except (OperationsRecordError, IdentityContractError, TypeError, ValueError):
        invalid = True
    if invalid:
        raise EffectPermissionDenied("original effect permission context is invalid")


def _standalone_context_matches(original, actor, workspace_id, *, actor_scopes=()):
    """Bind a real invocation to a closed retained intent, not historical scopes."""
    try:
        _original_intent(original)
    except (EffectPermissionDenied, AttributeError):
        return False
    return (original.principal.identity == actor
        and original.principal.identity.kind is PrincipalKind.OPERATOR
        and original.workspace_id == workspace_id
        and type(actor_scopes) is tuple
        and all(type(scope) is PolicyScope and scope in original.granted_scopes for scope in actor_scopes))


def _require_execution_binding(stores, context, request, run, authority, fence):
    """Read immutable parent provenance without reversing the command lock order."""
    if type(context) is not EffectCommandContext:
        raise EffectPermissionDenied("execution permission binding is unavailable")
    context.__post_init__()
    reference = context.receipt
    if reference.run_id != run.run_id or context.original.workspace_id != request.identity.workspace_id:
        raise EffectPermissionDenied("execution permission binding is unavailable")
    receipt = stores.execution.command_receipt_for_idempotency(reference.run_id, reference.idempotency_key)
    if (type(receipt) is not ExecutionCommandReceiptRecord
            or receipt.intent_fingerprint != reference.intent_fingerprint
            or receipt.status is not ExecutionCommandReceiptStatus.INCOMPLETE
            or receipt.managed_intent != _original_intent(context.original)
            or receipt.initial_run.admission.request_id != request.identity.request_id
            or receipt.initial_run.plan_id != run.plan_id
            or receipt.worker_id != authority.worker_id
            or receipt.worker_id != context.original.actor_id
            or receipt.authority_scopes != authority.scopes
            or receipt.claim_generation != fence.generation
            or receipt.worker_id != fence.worker_id
            or any(scope not in context.original.granted_scopes for scope in authority.scopes)):
        raise EffectPermissionDenied("execution permission binding is unavailable")
    return context.original
