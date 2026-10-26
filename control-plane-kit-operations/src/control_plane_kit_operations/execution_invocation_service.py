"""Command-owned semantic admission; capability IO belongs to the caller."""

from dataclasses import dataclass, field, replace
import hashlib
from typing import Protocol

import rfc8785

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.effect_permissions import (
    CurrentWorkspaceEffectPolicy, EffectCommandContext, _require_execution_binding,
)
from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationControlError, ExecutionInvocationConflict, ExecutionInvocationDenied,
    ExecutionInvocationExisting, ExecutionInvocationPlanned, ExecutionInvocationRecord, ExecutionInvocationDecision,
    ExecutionInvocationKind as Kind, ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationOutcome as Outcome, secret_invocation_target_fingerprint,
    signed_health_invocation_target_fingerprint,
)
from control_plane_kit_operations.health_effect_attempt_start import (
    StartHealthEffectAttempt, HealthEffectAttemptStartResult, _valid_health_command,
)
from control_plane_kit_operations.health_effect_preparations import ReceiverHealthEffectPreparationRecord
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders
from control_plane_kit_operations.health_signing_authority import (
    HealthSigningAuthorityReloadService, ReloadHealthSigningAuthority,
    _lock_health_prefix, _observed_epoch,
)
from control_plane_kit_operations.managed_effect_plan import require_managed_effect_plan
from control_plane_kit_operations.managed_runtime_assignments import ManagedRuntimeSelection
from control_plane_kit_operations.ordinary_verification_invocations import (
    ordinary_verification_candidate, ordinary_verification_invocation_profile,
    ordinary_verification_authorization_command, require_ordinary_verification_authorization,
)
from control_plane_kit_operations.prepared_runtime_invocations import (
    prepared_runtime_invocation_profile, prepared_runtime_reconciliation_target_fingerprint,
    prepared_runtime_observation_supported,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    ReconcileEffectAttempt, _valid_reconcile_command, _require_current_claim, _require_historical_lineage,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt, _valid_start_command
from control_plane_kit_operations.runtime_secret_uses import OperationalSecretUse, secret_authorization_command
from control_plane_kit_operations.secret_providers import (
    AuthorizedSecretUse, AuthorizedSecretVersionUse, authorize_secret_use_in_unit_of_work, authorize_runtime_secret_command,
    authorize_secret_version_use_in_unit_of_work, validate_authorized_secret_use,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    _require_request_run, _require_current_authority, _require_fresh_effect_receiver_permission,
)
from control_plane_kit_operations.records import ActivityRunStatus, OperationSessionStatus


_KINDS = (Kind.ENDPOINT_OBSERVATION, Kind.SECRET_RESOLUTION, Kind.SIGNED_HEALTH_TRANSPORT)
_SCOPES = (PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ,
    PolicyScope.NODE_CONTROL_EXECUTE, PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE)


class BoundExecutionInvocations(Protocol):
    control_error: type[Exception]

    def admit(self, kind: str, *, authorization_id: str | None = None) -> object: ...
    def record_outcome(self, permit: object, outcome: str) -> None: ...


def _require(condition):
    if not condition:
        raise ExecutionInvocationConflict()


@dataclass(frozen=True, slots=True, repr=False)
class _Permit:
    owner: object
    record: ExecutionInvocationRecord


@dataclass(frozen=True, slots=True, repr=False)
class _BoundHealthInvocations:
    service: object
    context: EffectCommandContext
    start: StartHealthEffectAttempt
    started: HealthEffectAttemptStartResult
    selection: ManagedRuntimeSelection
    owner: object = field(default_factory=object)
    control_error = ExecutionInvocationControlError

    def admit(self, kind, *, authorization_id=None):
        return self.service._admit(self, kind, authorization_id)

    def record_outcome(self, permit, outcome):
        self.service._record_outcome(self, permit, outcome)


@dataclass(frozen=True, slots=True, repr=False)
class _BoundRuntimeInvocations:
    service: object
    context: EffectCommandContext
    start: StartEffectAttempt
    started: NewlyStarted
    marker: ExecutionInvocationRecord
    runtime: object
    profile: object
    owner: object = field(default_factory=object)
    control_error = ExecutionInvocationControlError

    def admit(self, kind, *, authorization_id=None):
        return self.service._admit_runtime(self, kind, authorization_id)

    def record_outcome(self, permit, outcome):
        self.service._record_outcome(self, permit, outcome)


@dataclass(frozen=True, slots=True, repr=False)
class _BoundOrdinaryVerificationInvocations(_BoundRuntimeInvocations):
    # Per-call public grant metadata only; never resolved values or a UoW.
    check_grants: tuple = ()

    def record_outcome(self, permit, outcome):
        _require(type(permit) is _Permit and (
            permit.record.kind is Kind.ORDINARY_VERIFICATION_ACQUISITION
            or (permit.record.kind is Kind.SECRET_RESOLUTION
                and all(permit.record.authorization_id != grant.authorization_id for _, grant in self.check_grants))))
        self.service._record_outcome(self, permit, outcome)

    def for_check(self, check_id):
        try:
            check = self.profile.check(check_id)
            grants = tuple(grant for identity, grant in self.check_grants if identity == check_id)
            _require(len(grants) == (0 if check.credential_use is None else 1))
            return _BoundOrdinaryVerificationCheck(self, check, grants[0] if grants else None)
        except Exception:
            raise ExecutionInvocationConflict() from None


@dataclass(frozen=True, slots=True, repr=False)
class _BoundOrdinaryVerificationCheck:
    parent: _BoundOrdinaryVerificationInvocations
    check: object
    credential_grant: object
    control_error = ExecutionInvocationControlError

    def admit(self, kind, *, authorization_id=None):
        return self.parent.service._admit_runtime(self.parent, kind, authorization_id, check=self.check)

    def record_outcome(self, permit, outcome):
        _require(type(permit) is _Permit and (
            (permit.record.kind is Kind.ORDINARY_VERIFICATION_CHECK
                and permit.record.target_fingerprint == self.check.target_fingerprint)
            or (permit.record.kind is Kind.SECRET_RESOLUTION and self.credential_grant is not None
                and permit.record.authorization_id == self.credential_grant.authorization_id)))
        self.parent.service._record_outcome(self.parent, permit, outcome, check=self.check)


@dataclass(frozen=True, slots=True, repr=False)
class _BoundReconciliationInvocations:
    service: object
    context: EffectCommandContext
    command: ReconcileEffectAttempt
    attempt: EffectAttemptRecord
    intent_record: EffectAttemptIntentRecord
    marker: ExecutionInvocationRecord
    runtime: object
    profile: object
    target: str
    owner: object = field(default_factory=object)
    control_error = ExecutionInvocationControlError

    def admit(self, kind, *, authorization_id=None):
        return self.service._admit_reconciliation(self, kind, authorization_id)

    def record_outcome(self, permit, outcome):
        self.service._record_outcome(self, permit, outcome)


class ExecutionInvocationService:
    def __init__(self, unit_of_work_factory, *, permission_policy, clock,
            health_receiver_decoders=HealthReceiverDecoders(())):
        _require(callable(unit_of_work_factory) and callable(clock)
            and type(permission_policy) is CurrentWorkspaceEffectPolicy
            and type(health_receiver_decoders) is HealthReceiverDecoders)
        permission_policy.__post_init__()
        self._unit_of_work_factory = unit_of_work_factory
        self._permission_policy, self._clock = permission_policy, clock
        self._health = HealthSigningAuthorityReloadService(unit_of_work_factory,
            health_receiver_decoders=health_receiver_decoders)

    @property
    def permission_policy(self) -> CurrentWorkspaceEffectPolicy:
        return self._permission_policy

    def bind_health(self, *, command_context, start, started, managed_target):
        failed = False
        try:
            _require(type(command_context) is EffectCommandContext and _valid_health_command(start)
                and type(started) is HealthEffectAttemptStartResult and type(started.start) is NewlyStarted
                and type(started.preparation) is ReceiverHealthEffectPreparationRecord
                and type(managed_target) is ManagedRuntimeSelection)
            command_context.__post_init__()
            started.__post_init__()
            managed_target.__post_init__()
            attempt = started.start.attempt
            _require(command_context.original == start.context
                and command_context.receipt.run_id == start.start.transition.identity.run_id.value
                and attempt.state.identity == start.start.transition.identity
                and attempt.state.request_fingerprint == start.start.transition.request_fingerprint
                and attempt.state.fence.worker_id == start.start.fence.worker_id
                and attempt.state.fence.generation == start.start.fence.generation
                and managed_target.installation_id == self._permission_policy.installation_id
                and managed_target.workspace_id == start.context.workspace_id
                and managed_target.runtime_id == start.start.intent.operation.target.runtime_id
                and managed_target.authority_ref == start.start.intent.authority_ref)
        except Exception:
            failed = True
        if failed:
            raise ExecutionInvocationConflict()
        return _BoundHealthInvocations(self, command_context, start, started, managed_target)

    def inspect_health_dispatch(self, bound: BoundExecutionInvocations) -> ExecutionInvocationRecord:
        """Read original final state; neither a permit nor a retained result."""
        control, marker = None, None
        try:
            _require(type(bound) is _BoundHealthInvocations and bound.service is self)
            with self._unit_of_work_factory() as uow:
                reference = bound.context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, _, _ = self._history(uow.stores, bound, Kind.SIGNED_HEALTH_TRANSPORT, None)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        return marker

    def _binding(self, stores, bound, *, fresh):
        start = (bound.command if type(bound) is _BoundReconciliationInvocations else
            bound.start if type(bound) in (_BoundRuntimeInvocations, _BoundOrdinaryVerificationInvocations) else bound.start.start)
        request = stores.execution.get_request(start.request_id)
        run = stores.execution.get_run(bound.context.receipt.run_id)
        return _require_execution_binding(stores, bound.context, request, run,
            start.authority, start.fence, require_incomplete=fresh)

    def _history(self, stores, bound, kind, authorization_id):
        """Only immutable owner reads here; command advisory is already held."""
        self._binding(stores, bound, fresh=False)
        attempt = bound.started.start.attempt
        identity = attempt.state.identity
        evidence = stores.effect_attempt_intents.get(identity)
        preparation = stores.health_effect_preparations.get(identity)
        _require(evidence.intent == bound.start.start.intent
            and evidence.original_start_event == attempt.original_start_event
            and preparation == bound.started.preparation)
        marker = stores.execution.command_invocation_for_slot(bound.context.receipt,
            activity_id=identity.activity_id, kind=Kind.SIGNED_HEALTH_TRANSPORT)
        expected = ExecutionInvocationRecord(bound.context.receipt, preparation.workspace_id,
            identity.activity_id, Kind.SIGNED_HEALTH_TRANSPORT,
            signed_health_invocation_target_fingerprint(preparation), attempt.original_start_event.occurred_at,
            attempt=identity.attempt, original_event_id=preparation.original_event_id,
            request_fingerprint=preparation.request_fingerprint)
        _require(marker is not None and marker.invocation_id == expected.invocation_id
            and marker.created_at == expected.created_at)
        if kind is Kind.SECRET_RESOLUTION:
            _require(authorization_id in (preparation.transit_authorization_id, preparation.workload_authorization_id))
            authorization = stores.secret_use_authorizations.get(preparation.workspace_id, authorization_id)
            target = secret_invocation_target_fingerprint(authorization)
        else:
            _require(authorization_id is None)
            target = (expected.target_fingerprint if kind is Kind.SIGNED_HEALTH_TRANSPORT else
                hashlib.sha256(rfc8785.dumps(dict(domain="cpk.health-endpoint-target.v1",
                    final_target_fingerprint=expected.target_fingerprint))).hexdigest())
        current = (marker if kind is Kind.SIGNED_HEALTH_TRANSPORT else
            stores.execution.command_invocation_for_slot(bound.context.receipt,
                activity_id=identity.activity_id, kind=kind, authorization_id=authorization_id))
        if current is not None:
            _require(current.target_fingerprint == target and current.attempt == identity.attempt
                and current.original_event_id == preparation.original_event_id
                and current.request_fingerprint == preparation.request_fingerprint)
        return marker, current, target

    def _admit(self, bound, kind, authorization_id):
        control, admitted = None, None
        try:
            _require(type(kind) is str)
            kind = Kind(kind)
            _require(kind in _KINDS)
            if kind is Kind.ENDPOINT_OBSERVATION:
                operation = bound.start.start.intent.operation
                _require(type(operation) is ObserveManagementBootstrap
                    and operation.stage is ManagementBootstrapStage.GATEWAY_LOCAL_READY)
            with self._unit_of_work_factory() as uow:
                stores = uow.stores
                reference = bound.context.receipt
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, current, target = self._history(stores, bound, kind, authorization_id)
                if current is not None and current.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(current.invocation_id)
                        if current.disposition is Disposition.DENIED else ExecutionInvocationExisting(current.invocation_id))
                elif marker.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(marker.invocation_id)
                        if marker.disposition is Disposition.DENIED else ExecutionInvocationExisting(marker.invocation_id))
                else:
                    start = bound.start.start
                    command = ReloadHealthSigningAuthority(start.request_id, bound.started.preparation.identity,
                        bound.context.original, start.authority, start.fence)
                    locator = stores.execution.get_request(command.request_id)
                    prefix = _lock_health_prefix(uow, locator, command.identity, command_reference=reference)
                    original = self._binding(stores, bound, fresh=True)
                    prepared = self._health._prepare_in_unit_of_work(uow, command, run_prefix=prefix)
                    _require(prefix.attempt == bound.started.start.attempt
                        and prepared.preparation == bound.started.preparation
                        and prepared.evidence.intent == start.intent)
                    pinned = require_managed_effect_plan(stores, prepared.evidence.intent, prefix.runs.request)
                    _require(bound.selection.authority_ref == pinned.authority_ref
                        and bound.selection.runtime_id == start.intent.operation.target.runtime_id)
                    decision = self._permission_policy.evaluate_in_unit_of_work(uow, original,
                        required_scopes=_SCOPES, managed_target=bound.selection)
                    if decision.allowed:
                        _, observation = self._health._reload_prepared_in_unit_of_work(uow, prepared)
                    else:
                        observation = stores.execution.observe_request_lease_for_update(command.request_id)
                        _observed_epoch(observation, prefix.runs.request)
                    selected = decision.managed_selection
                    evidence = ExecutionInvocationDecision(decision.permission_revision,
                        None if selected is None else selected.assignment_id,
                        None if selected is None else selected.revision, decision.denial_code)
                    if kind is Kind.SIGNED_HEALTH_TRANSPORT:
                        record = marker.decided(evidence, observation.observed_at)
                        _require(stores.execution.replace_command_invocation(marker, record))
                    else:
                        record = ExecutionInvocationRecord(reference, marker.workspace_id, marker.activity_id,
                            kind, target, observation.observed_at, attempt=marker.attempt,
                            original_event_id=marker.original_event_id, request_fingerprint=marker.request_fingerprint,
                            authorization_id=authorization_id,
                            disposition=Disposition.ADMITTED if decision.allowed else Disposition.DENIED,
                            decision_at=observation.observed_at, decision=evidence,
                            outcome=Outcome.PENDING if decision.allowed else None)
                        _require(stores.execution.add_command_invocation(record) == record)
                    uow.commit()
                    if decision.allowed:
                        admitted = record
                    else:
                        control = ExecutionInvocationDenied(record.invocation_id)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            # No capability has been called, and no failed/ambiguous commit
            # may return a permit. Do not relabel owner/storage failure as A/B denial.
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        _require(admitted is not None)
        return _Permit(bound.owner, admitted)

    def _record_outcome(self, bound, permit, outcome, *, check=None):
        control = None
        try:
            _require(type(permit) is _Permit and permit.owner is bound.owner
                and type(outcome) is str)
            outcome = Outcome(outcome)
            _require(outcome is not Outcome.PENDING)
            reference = bound.context.receipt
            _require(permit.record.command == reference)
            with self._unit_of_work_factory() as uow:
                stores = uow.stores
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                stores.execution.command_receipt_for_idempotency(reference.run_id,
                    reference.idempotency_key, for_update=True)
                self._binding(stores, bound, fresh=False)
                if type(bound) is _BoundOrdinaryVerificationInvocations:
                    _, _, _, authorizations, _ = self._ordinary_history(stores, bound)
                    if permit.record.kind is Kind.SECRET_RESOLUTION:
                        _require(any(role[0] == check and value is not None
                            and value.authorization_id == permit.record.authorization_id
                            for role, value in authorizations))
                    elif check is None:
                        _require(permit.record.kind is Kind.ORDINARY_VERIFICATION_ACQUISITION)
                    else:
                        _require(bound.profile.check(check.check_id) == check
                            and permit.record.kind is Kind.ORDINARY_VERIFICATION_CHECK
                            and permit.record.target_fingerprint == check.target_fingerprint)
                elif type(bound) is _BoundRuntimeInvocations:
                    self._runtime_history(stores, bound, Kind.PREPARED_RUNTIME_DISPATCH, None)
                elif type(bound) is _BoundReconciliationInvocations:
                    self._reconciliation_history(stores, bound, Kind.RECONCILIATION_OBSERVATION, None)
                current = stores.execution.command_invocation(reference, permit.record.invocation_id, for_update=True)
                _require(current is not None)
                if current.outcome is Outcome.PENDING:
                    _require(current == permit.record)
                    completed = current.completed(outcome, self._clock())
                    _require(stores.execution.replace_command_invocation(current, completed))
                else:
                    _require(current == permit.record.completed(outcome, current.completed_at))
                uow.commit()
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control


    def _runtime_material(self, stores, context, start, started):
        _require(type(context) is EffectCommandContext and type(start) is StartEffectAttempt
            and _valid_start_command(start) and type(started) is NewlyStarted)
        context.__post_init__()
        started.__post_init__()
        attempt = started.attempt
        identity = attempt.state.identity
        _require(context.receipt.run_id == identity.run_id.value
            and identity == start.transition.identity
            and attempt.state.request_fingerprint == start.transition.request_fingerprint
            and attempt.state.fence.worker_id == start.fence.worker_id
            and attempt.state.fence.generation == start.fence.generation)
        request = stores.execution.get_request(start.request_id)
        run = stores.execution.get_run(context.receipt.run_id)
        _require_execution_binding(stores, context, request, run, start.authority, start.fence,
            require_incomplete=False)
        evidence = stores.effect_attempt_intents.get(identity)
        _require(evidence.intent == start.intent and evidence.original_start_event == attempt.original_start_event)
        marker = stores.execution.command_invocation_for_slot(context.receipt,
            activity_id=identity.activity_id, kind=(Kind.ORDINARY_VERIFICATION_ACQUISITION
                if ordinary_verification_candidate(start.intent) else Kind.PREPARED_RUNTIME_DISPATCH))
        _require(marker is not None and marker.attempt == identity.attempt
            and marker.original_event_id == attempt.original_start_event.event_id
            and marker.request_fingerprint == attempt.state.request_fingerprint
            and marker.created_at == attempt.original_start_event.occurred_at)
        runtime = stores.runtime_authorities.get_registration(marker.workspace_id,
            start.intent.authority_ref, marker.runtime_registration_id)
        profile_builder = (ordinary_verification_invocation_profile if ordinary_verification_candidate(start.intent)
            else prepared_runtime_invocation_profile)
        profile = profile_builder(start.intent,
            original_event_id=marker.original_event_id, runtime_authority=runtime)
        _require(profile.target_fingerprint == marker.target_fingerprint)
        return marker, runtime, profile

    def bind_runtime(self, *, command_context, start, started) -> BoundExecutionInvocations:
        bound, failed = None, False
        try:
            _require(type(command_context) is EffectCommandContext)
            command_context.__post_init__()
            with self._unit_of_work_factory() as uow:
                reference = command_context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, runtime, profile = self._runtime_material(uow.stores, command_context, start, started)
                bound_type = (_BoundOrdinaryVerificationInvocations if ordinary_verification_candidate(start.intent)
                    else _BoundRuntimeInvocations)
                bound = bound_type(self, command_context, start, started, marker, runtime, profile)
        except Exception:
            failed = True
        if failed:
            raise ExecutionInvocationConflict()
        return bound

    def _runtime_history(self, stores, bound, kind, authorization_id):
        _require(type(bound) is _BoundRuntimeInvocations and bound.service is self)
        marker, runtime, profile = self._runtime_material(stores, bound.context, bound.start, bound.started)
        _require(marker.invocation_id == bound.marker.invocation_id and profile == bound.profile
            and replace(runtime, status=bound.runtime.status) == bound.runtime)
        authorized = None
        if kind is Kind.SECRET_RESOLUTION:
            authorized = stores.secret_use_authorizations.get(marker.workspace_id, authorization_id)
            validate_authorized_secret_use(authorized)
            use = (authorized.selection if type(authorized) is AuthorizedSecretVersionUse
                else OperationalSecretUse(authorized.reference, authorized.intent))
            request = stores.execution.get_request(bound.start.request_id)
            _require(use in profile.required_uses and authorized.session_id == request.identity.session_id)
            runtime_request = runtime_effect_request_for_intent(bound.start.intent,
                effect_id=marker.original_event_id, secret_resolution_grants=())
            expected = secret_authorization_command(runtime_request, use,
                actor_subject=bound.context.original.actor_id, requested_at=authorized.requested_at,
                actor_scopes=bound.context.original.granted_scopes, session_id=request.identity.session_id)
            role_fields = (("selection", "request_fingerprint", "selection_fingerprint")
                if type(authorized) is AuthorizedSecretVersionUse else ("reference", "intent", "probe_id"))
            for name in ("workspace_id", "actor_subject", "operation_id", "session_id", "run_id",
                    "activity_id", "effect_id", "correlation_id", *role_fields):
                _require(getattr(authorized, name) == getattr(expected, name))
            current = stores.execution.command_invocation_for_slot(bound.context.receipt,
                activity_id=marker.activity_id, kind=kind, authorization_id=authorization_id)
            target = secret_invocation_target_fingerprint(authorized)
        else:
            _require(kind is Kind.PREPARED_RUNTIME_DISPATCH and authorization_id is None)
            current, target = marker, marker.target_fingerprint
        if current is not None:
            _require(current.target_fingerprint == target and current.attempt == marker.attempt
                and current.original_event_id == marker.original_event_id
                and current.request_fingerprint == marker.request_fingerprint)
        return marker, current, target, authorized

    def inspect_runtime_dispatch(self, bound: BoundExecutionInvocations) -> ExecutionInvocationRecord:
        marker, failed = None, False
        try:
            _require(type(bound) is _BoundRuntimeInvocations and bound.service is self)
            with self._unit_of_work_factory() as uow:
                reference = bound.context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, _, _, _ = self._runtime_history(uow.stores, bound, Kind.PREPARED_RUNTIME_DISPATCH, None)
        except Exception:
            failed = True
        if failed:
            raise ExecutionInvocationConflict()
        return marker

    def _admit_runtime(self, bound, kind, authorization_id, *, check=None):
        admitted, control = None, None
        try:
            ordinary = type(bound) is _BoundOrdinaryVerificationInvocations
            _require(type(kind) is str and (ordinary or type(bound) is _BoundRuntimeInvocations) and bound.service is self)
            kind = Kind(kind)
            _require(kind in ((Kind.SECRET_RESOLUTION, Kind.ORDINARY_VERIFICATION_ACQUISITION,
                Kind.ORDINARY_VERIFICATION_CHECK) if ordinary else (Kind.SECRET_RESOLUTION, Kind.PREPARED_RUNTIME_DISPATCH)))
            _require(ordinary or check is None)
            with self._unit_of_work_factory() as uow:
                stores, reference = uow.stores, bound.context.receipt
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                if ordinary:
                    marker, current, target, authorized = self._ordinary_admission_history(
                        stores, bound, kind, authorization_id, check)
                else:
                    marker, current, target, authorized = self._runtime_history(stores, bound, kind, authorization_id)
                if current is not None and current.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(current.invocation_id)
                        if current.disposition is Disposition.DENIED else ExecutionInvocationExisting(current.invocation_id))
                elif not ordinary and marker.disposition is not Disposition.PLANNED:
                    control = (ExecutionInvocationDenied(marker.invocation_id)
                        if marker.disposition is Disposition.DENIED else ExecutionInvocationExisting(marker.invocation_id))
                else:
                    guard = stores.graphs.lock_receiver_lifecycle(marker.workspace_id)
                    request = stores.execution.get_request_for_update(bound.start.request_id)
                    prefix = _lock_effect_run_prefix(uow, request, reference.run_id, latest_required=True)
                    stores.execution.command_receipt_for_idempotency(reference.run_id,
                        reference.idempotency_key, for_update=True)
                    attempt = stores.effect_attempts.get_for_update(bound.started.attempt.state.identity)
                    _require(attempt == bound.started.attempt and prefix.latest_run == prefix.requested_run
                        and prefix.requested_run.status is ActivityRunStatus.RUNNING)
                    _require_request_run(bound.start, request, prefix.requested_run)
                    _require_current_authority(bound.start, request)
                    session = stores.activity_history.get_session_for_update(request.identity.session_id)
                    _require(session.status is OperationSessionStatus.OPEN)
                    original = self._binding(stores, bound, fresh=True)
                    scopes = (PolicyScope.EXECUTION_OPERATE,)
                    if kind is Kind.SECRET_RESOLUTION:
                        scopes += (PolicyScope.SECRET_PROVIDER_USE,)
                    decision = self._permission_policy.evaluate_in_unit_of_work(uow, original, required_scopes=scopes)
                    _require_fresh_effect_receiver_permission(stores, request, guard, bound.start.intent, compensation=False)
                    if decision.allowed:
                        runtime = stores.runtime_authorities.get_registration_for_update(marker.workspace_id,
                            bound.start.intent.authority_ref, marker.runtime_registration_id)
                        _require(runtime.status.value == "active"
                            and replace(runtime, status=bound.runtime.status) == bound.runtime)
                        if authorized is not None:
                            runtime_request = runtime_effect_request_for_intent(bound.start.intent,
                                effect_id=marker.original_event_id, secret_resolution_grants=())
                            versioned = type(authorized) is AuthorizedSecretVersionUse
                            use = (authorized.selection if versioned
                                else OperationalSecretUse(authorized.reference, authorized.intent))
                            command = secret_authorization_command(runtime_request, use,
                                actor_subject=original.actor_id, requested_at=authorized.requested_at,
                                actor_scopes=decision.effective_scopes, session_id=request.identity.session_id,
                                probe_id=authorized.probe_id if ordinary else None)
                            authorize = (authorize_secret_version_use_in_unit_of_work if versioned
                                else authorize_secret_use_in_unit_of_work)
                            retained, _ = authorize(uow, command)
                            _require(retained == authorized)
                    observation = stores.execution.observe_request_lease_for_update(request.identity.request_id)
                    _require(observation.request == request and not observation.expired)
                    evidence = ExecutionInvocationDecision(decision.permission_revision, denial_code=decision.denial_code)
                    if kind in (Kind.PREPARED_RUNTIME_DISPATCH, Kind.ORDINARY_VERIFICATION_ACQUISITION):
                        record = marker.decided(evidence, observation.observed_at)
                        _require(stores.execution.replace_command_invocation(marker, record))
                    else:
                        record = ExecutionInvocationRecord(reference, marker.workspace_id, marker.activity_id,
                            kind, target, observation.observed_at, attempt=marker.attempt,
                            original_event_id=marker.original_event_id, request_fingerprint=marker.request_fingerprint,
                            authorization_id=authorization_id, disposition=Disposition.ADMITTED if decision.allowed else Disposition.DENIED,
                            decision_at=observation.observed_at, decision=evidence,
                            outcome=Outcome.PENDING if decision.allowed else None)
                        _require(stores.execution.add_command_invocation(record) == record)
                    uow.commit()
                    if decision.allowed:
                        admitted = record
                    else:
                        control = ExecutionInvocationDenied(record.invocation_id)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        _require(admitted is not None)
        return _Permit(bound.owner, admitted)


    def authorize_ordinary_verification(self, bound, request, authorizer):
        """Authorize declared metadata eagerly; secret bytes remain per reached check."""
        _require(type(bound) is _BoundOrdinaryVerificationInvocations and bound.service is self)
        expected = runtime_effect_request_for_intent(bound.start.intent,
            effect_id=bound.marker.original_event_id, secret_resolution_grants=())
        _require(request == expected)
        with self._unit_of_work_factory() as uow:
            operation = uow.stores.execution.get_request(bound.start.request_id)
        connection_grants, check_grants = [], []
        for role in bound.profile.secret_roles:
            _require(authorizer is not None)
            command = ordinary_verification_authorization_command(request, role,
                actor_subject=bound.context.original.actor_id, session_id=operation.identity.session_id,
                requested_at=bound.marker.created_at, actor_scopes=bound.context.original.granted_scopes)
            grant = authorize_runtime_secret_command(authorizer, command)
            if role[0] is None:
                connection_grants.append(grant)
            else:
                check_grants.append((role[0].check_id, grant))
        return (replace(request, secret_resolution_grants=tuple(connection_grants)),
            replace(bound, check_grants=tuple(check_grants)))

    def _ordinary_history(self, stores, bound):
        """Validate the complete closed family, including an unfinished frontier."""
        _require(type(bound) is _BoundOrdinaryVerificationInvocations and bound.service is self)
        marker, runtime, profile = self._runtime_material(stores, bound.context, bound.start, bound.started)
        _require(marker.invocation_id == bound.marker.invocation_id and profile == bound.profile
            and replace(runtime, status=bound.runtime.status) == bound.runtime)
        request = stores.execution.get_request(bound.start.request_id)
        runtime_request = runtime_effect_request_for_intent(bound.start.intent,
            effect_id=marker.original_event_id, secret_resolution_grants=())
        authorizations = []
        for role in profile.secret_roles:
            command = ordinary_verification_authorization_command(runtime_request, role,
                actor_subject=bound.context.original.actor_id, session_id=request.identity.session_id,
                requested_at=marker.created_at, actor_scopes=bound.context.original.granted_scopes)
            authorized = stores.secret_use_authorizations.for_correlation(marker.workspace_id, command.correlation_id)
            if authorized is not None:
                require_ordinary_verification_authorization(authorized, command)
            authorizations.append((role, authorized))
        records = stores.execution.ordinary_verification_invocations(marker, profile)
        _require(sum(row.invocation_id == marker.invocation_id for row in records) == 1)
        checks, resolutions = {}, {}
        for row in records:
            _require(row.attempt == marker.attempt and row.original_event_id == marker.original_event_id
                and row.request_fingerprint == marker.request_fingerprint)
            if row.kind is Kind.ORDINARY_VERIFICATION_ACQUISITION:
                _require(row == marker)
            elif row.kind is Kind.ORDINARY_VERIFICATION_CHECK:
                _require(row.target_fingerprint in tuple(check.target_fingerprint for check in profile.checks)
                    and row.target_fingerprint not in checks)
                checks[row.target_fingerprint] = row
            else:
                matches = tuple(authorized for _, authorized in authorizations
                    if authorized is not None and authorized.authorization_id == row.authorization_id)
                _require(len(matches) == 1 and row.target_fingerprint == secret_invocation_target_fingerprint(matches[0])
                    and row.authorization_id not in resolutions)
                resolutions[row.authorization_id] = row

        def resolution(role):
            authorized = next(value for candidate, value in authorizations if candidate == role)
            return None if authorized is None else resolutions.get(authorized.authorization_id)

        tls = tuple(resolution(role) for role in profile.secret_roles if role[0] is None)
        if marker.disposition is not Disposition.PLANNED:
            _require(all(self._returned(row) for row in tls))
        prefix_returned = True
        for check in profile.checks:
            row = checks.get(check.target_fingerprint)
            credential = resolution((check, check.credential_use)) if check.credential_use is not None else None
            if row is not None or credential is not None:
                _require(prefix_returned and self._returned(marker))
            if row is not None and check.credential_use is not None:
                _require(self._returned(credential))
            prefix_returned = prefix_returned and self._returned(row)
        return marker, checks, resolutions, tuple(authorizations), records

    @staticmethod
    def _returned(record):
        return (record is not None and record.disposition is Disposition.ADMITTED
            and record.outcome is Outcome.RETURNED)

    def _ordinary_admission_history(self, stores, bound, kind, authorization_id, check):
        marker, checks, resolutions, authorizations, _ = self._ordinary_history(stores, bound)
        authorized = None
        if check is not None:
            _require(bound.profile.check(check.check_id) == check
                and kind in (Kind.SECRET_RESOLUTION, Kind.ORDINARY_VERIFICATION_CHECK))
        else:
            _require(kind in (Kind.SECRET_RESOLUTION, Kind.ORDINARY_VERIFICATION_ACQUISITION))
        if kind is Kind.SECRET_RESOLUTION:
            matches = tuple(value for role, value in authorizations if role[0] == check
                and value is not None and value.authorization_id == authorization_id)
            _require(len(matches) == 1)
            authorized, = matches
            current, target = resolutions.get(authorization_id), secret_invocation_target_fingerprint(authorized)
        elif kind is Kind.ORDINARY_VERIFICATION_CHECK:
            _require(authorization_id is None and check is not None)
            current, target = checks.get(check.target_fingerprint), check.target_fingerprint
        else:
            _require(authorization_id is None and check is None)
            current, target = marker, marker.target_fingerprint
        if current is not None and current.disposition is not Disposition.PLANNED:
            return marker, current, target, authorized
        if check is None:
            _require(marker.disposition is Disposition.PLANNED)
            if kind is Kind.ORDINARY_VERIFICATION_ACQUISITION:
                for role, value in authorizations:
                    if role[0] is None:
                        _require(value is not None and self._returned(resolutions.get(value.authorization_id)))
        else:
            _require(self._returned(marker))
            for previous in bound.profile.checks:
                if previous == check:
                    break
                _require(self._returned(checks.get(previous.target_fingerprint)))
            if kind is Kind.SECRET_RESOLUTION:
                _require(check.target_fingerprint not in checks)
            elif check.credential_use is not None:
                value = next(value for role, value in authorizations if role[0] == check)
                _require(value is not None and self._returned(resolutions.get(value.authorization_id)))
        return marker, current, target, authorized

    def inspect_ordinary_verification(self, bound, result=None):
        """Coverage is necessary for success; only the typed result owns health truth."""
        from control_plane_kit_core.operations.execution import EffectResultKind
        control, marker = None, None
        try:
            with self._unit_of_work_factory() as uow:
                reference = bound.context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, checks, resolutions, authorizations, records = self._ordinary_history(uow.stores, bound)
                _require(not any(row.outcome is Outcome.PENDING for row in records))
                if any(row.outcome is Outcome.UNKNOWN for row in records):
                    _require(result is not None and result.kind is EffectResultKind.UNCERTAIN)
                denied = next((row for row in records if row.disposition is Disposition.DENIED), None)
                if denied is not None:
                    raise ExecutionInvocationDenied(denied.invocation_id)
                if marker.disposition is Disposition.PLANNED:
                    raise ExecutionInvocationPlanned(marker.invocation_id)
                if result is not None and result.kind is EffectResultKind.SUCCEEDED:
                    _require(self._returned(marker)
                        and all(self._returned(checks.get(check.target_fingerprint)) for check in bound.profile.checks)
                        and all(value is not None and self._returned(resolutions.get(value.authorization_id))
                            for _, value in authorizations))
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        return marker

    def _reconciliation_material(self, stores, context, command, attempt, intent_record, runtime_authority):
        _require(type(context) is EffectCommandContext and _valid_reconcile_command(command)
            and type(attempt) is EffectAttemptRecord and type(intent_record) is EffectAttemptIntentRecord)
        context.__post_init__()
        attempt.__post_init__()
        intent_record.__post_init__()
        identity = command.identity
        _require(attempt.state.identity == identity and intent_record.identity == identity
            and intent_record.original_start_event == attempt.original_start_event
            and intent_record.request_fingerprint == attempt.state.request_fingerprint
            and context.receipt.run_id == identity.run_id.value)
        _require_historical_lineage(command, attempt)
        request = stores.execution.get_request(command.request_id)
        run = stores.execution.get_run(context.receipt.run_id)
        _require_execution_binding(stores, context, request, run, command.authority, command.fence,
            require_incomplete=False)
        _require(intent_record == stores.effect_attempt_intents.get(identity)
            and intent_record.request_id == command.request_id
            and intent_record.workspace_id == request.identity.workspace_id
            and intent_record.intent.source.plan_id == run.plan_id)
        marker = stores.execution.original_final_invocation(identity,
            original_event_id=attempt.original_start_event.event_id,
            request_fingerprint=attempt.state.request_fingerprint, kind=Kind.PREPARED_RUNTIME_DISPATCH)
        _require(marker is not None and marker.command != context.receipt)
        if marker.disposition is Disposition.PLANNED:
            raise ExecutionInvocationPlanned(marker.invocation_id)
        if marker.disposition is Disposition.DENIED:
            raise ExecutionInvocationDenied(marker.invocation_id)
        _require(marker.disposition is Disposition.ADMITTED)
        runtime = stores.runtime_authorities.get_registration(marker.workspace_id,
            intent_record.intent.authority_ref, marker.runtime_registration_id)
        _require(type(runtime_authority) is type(runtime)
            and replace(runtime, status=runtime_authority.status) == runtime_authority)
        profile = prepared_runtime_invocation_profile(intent_record.intent,
            original_event_id=marker.original_event_id, runtime_authority=runtime)
        _require(profile.target_fingerprint == marker.target_fingerprint)
        _require(prepared_runtime_observation_supported(intent_record.intent))
        target = prepared_runtime_reconciliation_target_fingerprint(
            original_invocation_id=marker.invocation_id, original_target_fingerprint=marker.target_fingerprint,
            original_request_fingerprint=marker.request_fingerprint, runtime_registration_id=marker.runtime_registration_id)
        return marker, runtime, profile, target

    def bind_reconciliation(self, *, command_context, command, attempt, intent_record,
            runtime_authority) -> BoundExecutionInvocations:
        bound, control = None, None
        try:
            _require(type(command_context) is EffectCommandContext)
            command_context.__post_init__()
            with self._unit_of_work_factory() as uow:
                reference = command_context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, runtime, profile, target = self._reconciliation_material(uow.stores,
                    command_context, command, attempt, intent_record, runtime_authority)
                _require(attempt.state.status is EffectAttemptStatus.STARTED
                    and uow.stores.effect_attempts.get(command.identity) == attempt)
                bound = _BoundReconciliationInvocations(self, command_context, command,
                    attempt, intent_record, marker, runtime, profile, target)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        return bound

    def _reconciliation_history(self, stores, bound, kind, authorization_id):
        _require(type(bound) is _BoundReconciliationInvocations and bound.service is self)
        marker, runtime, profile, target = self._reconciliation_material(stores, bound.context,
            bound.command, bound.attempt, bound.intent_record, bound.runtime)
        _require(marker.invocation_id == bound.marker.invocation_id and profile == bound.profile
            and target == bound.target and replace(runtime, status=bound.runtime.status) == bound.runtime)
        authorized = None
        if kind is Kind.SECRET_RESOLUTION:
            authorized = stores.secret_use_authorizations.get(marker.workspace_id, authorization_id)
            validate_authorized_secret_use(authorized)
            _require(type(authorized) is AuthorizedSecretUse)
            use = OperationalSecretUse(authorized.reference, authorized.intent)
            request = stores.execution.get_request(bound.command.request_id)
            _require(use in profile.required_uses and authorized.session_id == request.identity.session_id)
            runtime_request = runtime_effect_request_for_intent(bound.intent_record.intent,
                effect_id=marker.original_event_id, secret_resolution_grants=())
            expected = secret_authorization_command(runtime_request, use,
                actor_subject=bound.context.original.actor_id, requested_at=authorized.requested_at,
                actor_scopes=bound.context.original.granted_scopes, session_id=request.identity.session_id)
            for name in ("workspace_id", "actor_subject", "operation_id", "session_id", "run_id",
                    "activity_id", "effect_id", "reference", "intent", "correlation_id", "probe_id"):
                _require(getattr(authorized, name) == getattr(expected, name))
            target = secret_invocation_target_fingerprint(authorized)
        else:
            _require(kind is Kind.RECONCILIATION_OBSERVATION and authorization_id is None)
        current = stores.execution.command_invocation_for_slot(bound.context.receipt,
            activity_id=marker.activity_id, kind=kind, authorization_id=authorization_id)
        if current is not None:
            _require(current.target_fingerprint == target and current.attempt == marker.attempt
                and current.original_event_id == marker.original_event_id
                and current.request_fingerprint == marker.request_fingerprint)
        return marker, current, target, authorized

    def inspect_reconciliation_observation(self, bound: BoundExecutionInvocations) -> ExecutionInvocationRecord | None:
        current, control = None, None
        try:
            _require(type(bound) is _BoundReconciliationInvocations and bound.service is self)
            with self._unit_of_work_factory() as uow:
                reference = bound.context.receipt
                uow.stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                _, current, _, _ = self._reconciliation_history(uow.stores, bound,
                    Kind.RECONCILIATION_OBSERVATION, None)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        return current

    def _admit_reconciliation(self, bound, kind, authorization_id):
        admitted, control = None, None
        try:
            _require(type(kind) is str and type(bound) is _BoundReconciliationInvocations and bound.service is self)
            kind = Kind(kind)
            _require(kind in (Kind.SECRET_RESOLUTION, Kind.RECONCILIATION_OBSERVATION))
            with self._unit_of_work_factory() as uow:
                stores, reference = uow.stores, bound.context.receipt
                stores.execution.lock_command_idempotency(reference.run_id, reference.idempotency_key)
                marker, current, target, authorized = self._reconciliation_history(stores, bound, kind, authorization_id)
                final = (current if kind is Kind.RECONCILIATION_OBSERVATION else
                    self._reconciliation_history(stores, bound, Kind.RECONCILIATION_OBSERVATION, None)[1])
                existing = current if current is not None else final
                if existing is not None:
                    control = (ExecutionInvocationDenied(existing.invocation_id)
                        if existing.disposition is Disposition.DENIED else ExecutionInvocationExisting(existing.invocation_id))
                else:
                    guard = stores.graphs.lock_receiver_lifecycle(marker.workspace_id)
                    request = stores.execution.get_request_for_update(bound.command.request_id)
                    prefix = _lock_effect_run_prefix(uow, request, reference.run_id, latest_required=True)
                    stores.execution.command_receipt_for_idempotency(reference.run_id,
                        reference.idempotency_key, for_update=True)
                    attempt = stores.effect_attempts.get_for_update(bound.command.identity)
                    _require(attempt == bound.attempt and attempt.state.status is EffectAttemptStatus.STARTED
                        and prefix.latest_run == prefix.requested_run
                        and prefix.requested_run.status is ActivityRunStatus.RUNNING)
                    _require_current_claim(bound.command, request, prefix.requested_run, attempt)
                    _require_historical_lineage(bound.command, attempt)
                    session = stores.activity_history.get_session_for_update(request.identity.session_id)
                    _require(session.status is OperationSessionStatus.OPEN)
                    original = self._binding(stores, bound, fresh=True)
                    scopes = (PolicyScope.EXECUTION_OPERATE,)
                    if kind is Kind.SECRET_RESOLUTION:
                        scopes += (PolicyScope.SECRET_PROVIDER_USE,)
                    decision = self._permission_policy.evaluate_in_unit_of_work(uow, original, required_scopes=scopes)
                    _require_fresh_effect_receiver_permission(stores, request, guard,
                        bound.intent_record.intent, compensation=False)
                    if decision.allowed:
                        runtime = stores.runtime_authorities.get_registration_for_update(marker.workspace_id,
                            bound.intent_record.intent.authority_ref, marker.runtime_registration_id)
                        _require(runtime.status.value == "active"
                            and replace(runtime, status=bound.runtime.status) == bound.runtime)
                        if authorized is not None:
                            runtime_request = runtime_effect_request_for_intent(bound.intent_record.intent,
                                effect_id=marker.original_event_id, secret_resolution_grants=())
                            use = OperationalSecretUse(authorized.reference, authorized.intent)
                            authorization = secret_authorization_command(runtime_request, use,
                                actor_subject=original.actor_id, requested_at=authorized.requested_at,
                                actor_scopes=decision.effective_scopes, session_id=request.identity.session_id)
                            retained, _ = authorize_secret_use_in_unit_of_work(uow, authorization)
                            _require(retained == authorized)
                    observation = stores.execution.observe_request_lease_for_update(request.identity.request_id)
                    _require(observation.request == request and not observation.expired)
                    evidence = ExecutionInvocationDecision(decision.permission_revision, denial_code=decision.denial_code)
                    record = ExecutionInvocationRecord(reference, marker.workspace_id, marker.activity_id,
                        kind, target, observation.observed_at, attempt=marker.attempt,
                        original_event_id=marker.original_event_id, request_fingerprint=marker.request_fingerprint,
                        authorization_id=authorization_id, disposition=Disposition.ADMITTED if decision.allowed else Disposition.DENIED,
                        decision_at=observation.observed_at, decision=evidence,
                        outcome=Outcome.PENDING if decision.allowed else None)
                    _require(stores.execution.add_command_invocation(record) == record)
                    uow.commit()
                    if decision.allowed:
                        admitted = record
                    else:
                        control = ExecutionInvocationDenied(record.invocation_id)
        except ExecutionInvocationControlError as error:
            control = error
        except Exception:
            control = ExecutionInvocationConflict()
        if control is not None:
            raise control
        _require(admitted is not None)
        return _Permit(bound.owner, admitted)


__all__ = ["ExecutionInvocationService", "BoundExecutionInvocations"]
